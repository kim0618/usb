"""Binance user data stream: a change signal, not a source of truth.

Binance pushes `ACCOUNT_UPDATE` and `ORDER_TRADE_UPDATE` frames that carry balances and fills.
This class reads the *event type* and treats every account-affecting frame as one instruction:
**re-read the account over REST**. It does not apply a balance from a socket frame.

That is a deliberate narrowing, and the reason is §10 of the LIVE contract: Binance's REST
answer is the authority for balance, position, leverage and margin mode. A socket frame can be
missed during a reconnect, can arrive out of order relative to a REST read already in flight,
and would leave two code paths able to move the same figure. One authority, one path, and the
socket's job is only to say "now", so the panel updates within a tick of a fill instead of
waiting for the next poll.

Lifecycle, in the order Binance documents it (POST/PUT/DELETE `/fapi/v1/listenKey`, verified
live on 2026-09-29 - all three answer `-2014` to an unsigned call, so all three exist):

    POST listenKey -> connect <private base>?listenKey=<key> -> PUT every 30 min -> DELETE on shutdown

The key goes in the **query string**, not in the path. The legacy `<base>/<listenKey>` form was
decommissioned on 2026-04-23 (see `endpoints.DEFAULT_WS_PRIVATE_URL`) and fails silently rather
than loudly: it still completes a handshake and answers ping, so a stream on it looks connected
forever and delivers nothing.

No `events` filter is sent, and that is deliberate. Binance accepts
`&events=ORDER_TRADE_UPDATE/ACCOUNT_UPDATE` and honours it strictly - a socket asking for
`ORDER_TRADE_UPDATE` alone was measured here receiving no `ACCOUNT_CONFIG_UPDATE` at all.
Enumerating the events we know about today would therefore drop `listenKeyExpired` and
`MARGIN_CALL` the moment they matter most, which is the same shape of failure this module just
came out of. Omitting the parameter delivers every event, and `handle` does the filtering where
the set is visible and tested.
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

import websockets

from .credentials import LiveConfig
from .mirror import LiveEvent, LiveMirror
from .rest import BinanceError, BinanceFuturesClient

#: Binance expires a listen key 60 minutes after the last keepalive. Half that, so one missed
#: keepalive is not a dropped stream.
KEEPALIVE_INTERVAL_S = 1_800
#: Frames that mean the account changed. Anything else is counted and ignored.
ACCOUNT_EVENTS = ("ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "ACCOUNT_CONFIG_UPDATE",
                  "MARGIN_CALL", "TRADE_LITE")
EXPIRED_EVENT = "listenKeyExpired"
RECV_TIMEOUT_S = 30.0


@dataclass
class StreamTelemetry:
    connected: bool = False
    connects: int = 0
    reconnects: int = 0
    messages: int = 0
    account_events: int = 0
    keepalives: int = 0
    key_renewals: int = 0
    expired_events: int = 0
    malformed: int = 0
    last_event_type: str | None = None
    last_message_ms: int | None = None
    last_error: str | None = None
    started_ms: int | None = None
    #: Set whenever a frame says the account moved; cleared by the reconciler once it has
    #: re-read Binance. The panel shows it so a pending reconcile is visible rather than
    #: silently late.
    dirty: bool = False
    dirty_since_ms: int | None = None

    def view(self) -> dict[str, Any]:
        return {"connected": self.connected, "connects": self.connects,
                "reconnects": self.reconnects, "messages": self.messages,
                "account_events": self.account_events, "keepalives": self.keepalives,
                "key_renewals": self.key_renewals, "expired_events": self.expired_events,
                "malformed": self.malformed, "last_event_type": self.last_event_type,
                "last_message_ms": self.last_message_ms, "last_error": self.last_error,
                "started_ms": self.started_ms, "dirty": self.dirty,
                "dirty_since_ms": self.dirty_since_ms,
                "role": "CHANGE_SIGNAL_ONLY: balances are re-read over REST, never taken from a frame"}


@dataclass
class UserDataStream:
    """One socket per account, on its own thread, isolated from the paper terminal's feeds."""
    client: BinanceFuturesClient
    config: LiveConfig
    mirror: LiveMirror | None = None
    #: Called on the stream's thread whenever the account is known to have changed. The adapter
    #: passes a function that re-reads the snapshot over REST.
    on_change: Callable[[str], None] | None = None
    connector: Callable[[str], Any] | None = None
    telemetry: StreamTelemetry = field(default_factory=StreamTelemetry)
    listen_key: str | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _last_keepalive: float = 0.0

    # ------------------------------------------------------------------ key lifecycle

    def open_key(self) -> str:
        payload = self.client.call("listen_key_create")
        key = str(payload.get("listenKey") or "")
        if not key:
            raise BinanceError(status=0, code=None, message="listenKey response carried no key")
        self.listen_key = key
        self.telemetry.key_renewals += 1
        self._last_keepalive = time.time()
        return key

    def keepalive(self) -> None:
        self.client.call("listen_key_keepalive")
        self.telemetry.keepalives += 1
        self._last_keepalive = time.time()

    def close_key(self) -> None:
        if self.listen_key is None:
            return
        try:
            self.client.call("listen_key_close")
        except BinanceError:
            # Shutdown path: the key expires on its own within the hour, and a failure to tidy
            # up must not stop the process from exiting.
            pass
        self.listen_key = None

    def url(self, key: str) -> str:
        """`<private base>?listenKey=<key>`, with no event filter. See the module docstring."""
        base = self.config.ws_private_url.rstrip("/")
        return f"{base}?{urlencode({'listenKey': key})}"

    # ------------------------------------------------------------------ frames

    def handle(self, frame: dict[str, Any]) -> None:
        self.telemetry.messages += 1
        self.telemetry.last_message_ms = int(time.time() * 1000)
        event = str(frame.get("e") or "")
        self.telemetry.last_event_type = event or None
        if event == EXPIRED_EVENT:
            self.telemetry.expired_events += 1
            self.listen_key = None
            self._record(event, note="listen key expired; a new one is minted on reconnect")
            self._mark_dirty(event)
            return
        if event in ACCOUNT_EVENTS:
            self.telemetry.account_events += 1
            self._record(event)
            self._mark_dirty(event)

    def _record(self, event: str, **extra: Any) -> None:
        if self.mirror is None:
            return
        # Event type and transaction time only. The frame's balances are deliberately not
        # copied into the audit file: recording a figure this package refuses to trust would
        # invite a later reader to trust it.
        self.mirror.append(LiveEvent.STREAM_EVENT, event=event, **extra)

    def _mark_dirty(self, event: str) -> None:
        self.telemetry.dirty = True
        if self.telemetry.dirty_since_ms is None:
            self.telemetry.dirty_since_ms = int(time.time() * 1000)
        if self.on_change is not None:
            try:
                self.on_change(event)
            except Exception as exc:  # a reconcile failure must not kill the socket
                self.telemetry.last_error = f"on_change: {type(exc).__name__}: {exc}"

    def clear_dirty(self) -> None:
        self.telemetry.dirty = False
        self.telemetry.dirty_since_ms = None

    # ------------------------------------------------------------------ loop

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                key = self.listen_key or self.open_key()
                factory = (self.connector or (lambda url: websockets.connect(
                    url, ping_interval=20, ping_timeout=20, max_queue=256)))
                async with factory(self.url(key)) as socket:
                    if attempt or self.telemetry.connects:
                        self.telemetry.reconnects += 1
                        # Whatever arrived while the socket was down was delivered to nobody.
                        self._mark_dirty("RECONNECT")
                    self.telemetry.connects += 1
                    self.telemetry.connected = True
                    attempt = 0
                    if self.mirror is not None:
                        self.mirror.append(LiveEvent.STREAM_STATE, state="CONNECTED")
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=RECV_TIMEOUT_S)
                        except asyncio.TimeoutError:
                            # Silence is normal on this stream: a quiet account sends nothing
                            # for hours. The keepalive is what proves the key is still alive.
                            self._maybe_keepalive()
                            continue
                        try:
                            self.handle(json.loads(raw))
                        except json.JSONDecodeError:
                            self.telemetry.malformed += 1
                        self._maybe_keepalive()
            except Exception as exc:  # isolation: nothing escapes this thread
                self.telemetry.last_error = f"{type(exc).__name__}: {exc}"
            self.telemetry.connected = False
            if self.mirror is not None and not self._stop.is_set():
                self.mirror.append(LiveEvent.STREAM_STATE, state="DISCONNECTED",
                                   error=self.telemetry.last_error)
            attempt += 1
            delay = min(30.0, 2 ** min(attempt, 5)) + random.random()
            end = time.time() + delay
            while not self._stop.is_set() and time.time() < end:
                await asyncio.sleep(0.2)

    def _maybe_keepalive(self) -> None:
        if time.time() - self._last_keepalive < KEEPALIVE_INTERVAL_S:
            return
        try:
            self.keepalive()
        except BinanceError as exc:
            # A refused keepalive means the key is gone. Drop it; the loop mints a new one.
            self.telemetry.last_error = f"keepalive: {exc}"
            self.listen_key = None
            raise

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.telemetry.started_ms = int(time.time() * 1000)
        self._thread = threading.Thread(target=self._thread_main, name="binance-user-stream",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.close_key()

    def view(self) -> dict[str, Any]:
        return {**self.telemetry.view(), "has_listen_key": self.listen_key is not None,
                "url": self.config.ws_private_url}


__all__ = ["UserDataStream", "StreamTelemetry", "ACCOUNT_EVENTS", "EXPIRED_EVENT",
           "KEEPALIVE_INTERVAL_S"]
