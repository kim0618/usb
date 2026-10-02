"""What leverage this account may actually select, and why the bracket table cannot say.

The case these tests hold is a real one, recorded on the live account on 2026-10-01: the
operator pressed 20x and it worked, pressed 50x and it was refused. The bracket table reached
150 both times, so nothing in the ladder could explain the difference. Binance could:

    POST /fapi/v1/leverage  ->  400  code -4300
    "You can start trading with more than 20x leverage by 2026-10-29 01:34 (UTC), because
     higher leverage is available 30 days after Futures account registration."

So the tests below assert three things about that refusal. It is learned rather than assumed,
it is generalised only as far as Binance's own sentence goes, and it lifts by itself at the
instant Binance named - a hardcoded 20x cap would satisfy the first screen and then be wrong
on 2026-10-29 with nobody to notice.

Nothing here sends an order. The one write exercised is the leverage endpoint, against the
`FakeBinance` transport.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.leverage import (ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE, LeverageCapability,
                                      parse_threshold, parse_until_ms)
from app.crypto.live.orders import LiveOrderRouter, OrderRefused
from app.crypto.live.account import AccountReader

from tests.crypto.binance_fixtures import (POSITION_RISK_FLAT, SYMBOL, FakeBinance, make_client,
                                           make_config)

#: Binance's own sentence, verbatim from the refusal recorded on the real account.
REFUSAL = ("You can start trading with more than 20x leverage by 2026-10-29 01:34 (UTC), "
           "because higher leverage is available 30 days after Futures account registration.")

#: 2026-10-29 01:34 UTC, the instant the message names.
LIFTS_AT_MS = 1_793_237_640_000

LADDER = (1, 2, 3, 5, 10, 20, 50, 100)


def router(*, trading_enabled: bool = True, path: Path | None = None):
    config = make_config(trading_enabled=trading_enabled)
    client, fake = make_client(trading_enabled=trading_enabled)
    fake.position_rows = POSITION_RISK_FLAT
    reader = AccountReader(client, config)
    subject = LiveOrderRouter(reader=reader, client=client, config=config,
                              capability=LeverageCapability(path=path))
    return subject, fake


# ------------------------------------------------------------------ reading Binance's sentence


def test_the_threshold_and_the_moment_are_read_out_of_binances_own_message() -> None:
    # Neither is inferred. An account-age cap is not something this package can calculate, and
    # a guessed date would either grey out a working button or offer a refused one.
    assert parse_threshold(REFUSAL) == 20
    assert parse_until_ms(REFUSAL) == LIFTS_AT_MS


def test_a_message_without_a_threshold_restricts_only_the_step_that_was_refused() -> None:
    """Generalising further would grey out buttons Binance never spoke about."""
    capability = LeverageCapability()
    capability.note_refusal(leverage=50, code=-4300, message="Leverage not available.")

    assert capability.restriction_for(50) is not None
    assert capability.restriction_for(100) is not None
    assert capability.restriction_for(20) is None
    assert capability.restriction_for(49) is None


def test_the_named_threshold_covers_every_step_above_it() -> None:
    capability = LeverageCapability()
    capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)

    assert set(capability.unavailable(LADDER)) == {"50", "100"}
    # 20x is what the operator just used successfully; it must not be greyed out by a refusal
    # about the steps above it.
    assert capability.restriction_for(20) is None


# ------------------------------------------------------------------ nothing is assumed


def test_a_fresh_capability_forbids_nothing_and_promises_nothing() -> None:
    capability = LeverageCapability()
    assert capability.unavailable(LADDER) == {}
    assert capability.restriction_for(100) is None


def test_only_the_restriction_family_narrows_the_ladder() -> None:
    """A bad number, an outage or an open position say nothing about capability. Learning from
    them would shrink the ladder for reasons that have nothing to do with the account."""
    capability = LeverageCapability()
    for code in (-4028, -2027, -1021, None):
        assert capability.note_refusal(leverage=50, code=code, message=REFUSAL) is None
    assert capability.unavailable(LADDER) == {}


# ------------------------------------------------------------------ it expires, and it yields


def test_the_restriction_lifts_at_the_instant_binance_named_without_a_deploy() -> None:
    capability = LeverageCapability()
    capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)

    assert capability.restriction_for(50, now_ms=LIFTS_AT_MS - 1) is not None
    assert capability.restriction_for(50, now_ms=LIFTS_AT_MS) is None
    assert capability.unavailable(LADDER, now_ms=LIFTS_AT_MS) == {}


def test_a_success_overrides_a_restriction_that_said_it_would_fail() -> None:
    capability = LeverageCapability()
    capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)
    capability.note_success(50)

    assert capability.unavailable(LADDER) == {}


def test_a_success_at_a_permitted_step_leaves_the_restriction_alone() -> None:
    capability = LeverageCapability()
    capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)
    capability.note_success(20)

    assert set(capability.unavailable(LADDER)) == {"50", "100"}


# ------------------------------------------------------------------ the message on screen


def test_the_operator_is_told_about_their_account_not_about_the_endpoint() -> None:
    capability = LeverageCapability()
    learned = capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)
    assert learned is not None
    shown = learned.message(50)

    assert "50x" in shown and "20x" in shown
    assert "2026-10-29 01:34 UTC" in shown
    # No endpoint, no error code, no English passthrough, no signature or key material.
    for leak in ("fapi", "-4300", "Futures account registration", "signature", "apiKey"):
        assert leak not in shown


# ------------------------------------------------------------------ the router


def test_the_router_learns_from_the_exchange_refusal_and_reports_it_as_a_refusal() -> None:
    subject, fake = router()
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)

    with pytest.raises(OrderRefused) as caught:
        subject.set_leverage(50)

    assert caught.value.code == ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE
    assert "2026-10-29 01:34 UTC" in caught.value.message
    assert set(subject.capability.unavailable(LADDER)) == {"50", "100"}


def test_the_second_press_is_refused_without_sending_the_write_again() -> None:
    """Once Binance has said no, pressing again is a write it will refuse identically. The
    refusal is replayed locally instead, so a greyed-out button that is pressed anyway - by a
    stale tab, say - does not cost a round trip to the real account."""
    subject, fake = router()
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)
    with pytest.raises(OrderRefused):
        subject.set_leverage(50)
    sent = fake.count("/fapi/v1/leverage")

    with pytest.raises(OrderRefused) as caught:
        subject.set_leverage(100)

    assert caught.value.code == ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE
    assert fake.count("/fapi/v1/leverage") == sent


def test_a_permitted_step_still_goes_through_after_a_higher_one_was_refused() -> None:
    # The whole point of learning the threshold rather than a flag: 20x is exactly what the
    # operator did next on the real account, and it worked.
    subject, fake = router()
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)
    with pytest.raises(OrderRefused):
        subject.set_leverage(50)
    fake.errors.pop(("POST", "/fapi/v1/leverage"))

    assert subject.set_leverage(20)["leverage"] == 5  # SET_LEVERAGE fixture's own response
    assert subject.capability.restriction_for(20) is None


def test_an_unrelated_exchange_error_is_not_turned_into_a_capability_claim() -> None:
    subject, fake = router()
    fake.fail("POST", "/fapi/v1/leverage", 400, -4028, "Leverage 50 is not valid")

    with pytest.raises(Exception) as caught:
        subject.set_leverage(50)

    assert not isinstance(caught.value, OrderRefused)
    assert subject.capability.unavailable(LADDER) == {}


def test_the_learned_restriction_survives_a_restart(tmp_path: Path) -> None:
    """Without the file every deploy would cost the operator another refused click to
    rediscover the same limit."""
    path = tmp_path / "leverage_capability.json"
    subject, fake = router(path=path)
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)
    with pytest.raises(OrderRefused):
        subject.set_leverage(50)

    reloaded = LeverageCapability(path=path)
    assert set(reloaded.unavailable(LADDER)) == {"50", "100"}
    stored = json.loads(path.read_text())
    assert stored["restrictions"][0]["above"] == 20
    # No credential of any kind in the file.
    raw = path.read_text()
    for leak in ("test-api-key-0123456789", "test-api-secret-abcdef"):
        assert leak not in raw


def test_a_damaged_capability_file_does_not_read_as_everything_allowed(tmp_path: Path) -> None:
    path = tmp_path / "leverage_capability.json"
    path.write_text("{broken")

    capability = LeverageCapability(path=path)

    # Same state a fresh process is in: nothing known, and the next refusal relearns it. The
    # alternative - treating a damaged file as a restriction - would grey out a working ladder
    # with no way for the operator to clear it.
    assert capability.unavailable(LADDER) == {}


def test_a_position_still_blocks_the_change_before_capability_is_consulted() -> None:
    """The existing policy is untouched: leverage is not changed while a position is held, and
    that refusal comes first because it is the one the operator can act on immediately."""
    subject, fake = router()
    fake.position_rows = [{**POSITION_RISK_FLAT[0], "positionAmt": "0.015",
                           "entryPrice": "82666.66", "notional": "1252.50"}]

    with pytest.raises(OrderRefused) as caught:
        subject.set_leverage(50)

    assert caught.value.code == "LEVERAGE_POSITION_OPEN"
    assert fake.count("/fapi/v1/leverage") == 0


def test_the_api_reports_the_restriction_beside_the_options(tmp_path: Path, live) -> None:
    """The ladder keeps the step and says why it is unavailable. A step dropped from the list
    would look like a symbol limit, which is a different problem with a different remedy."""
    client, adapter, fake, *_ = live
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)
    adapter.router.capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)

    body = client.get("/api/crypto/binance/leverage").json()

    assert 50 in body["options"] and 100 in body["options"]
    assert set(body["unavailable"]) == {"50", "100"}
    assert body["unavailable"]["50"]["code_name"] == ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE
    assert body["unavailable"]["50"]["until_utc"] == "2026-10-29 01:34 UTC"
    assert body["max_leverage"] == 150  # the symbol's ceiling, unchanged by the restriction


def test_the_route_answers_a_restricted_request_with_a_409_and_a_korean_reason(live) -> None:
    client, _adapter, fake, arm, *_ = live
    arm.arm(confirmation="ARM LIVE TRADING")
    fake.fail("POST", "/fapi/v1/leverage", 400, -4300, REFUSAL)

    response = client.post("/api/crypto/binance/leverage", json={"leverage": "50"})

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE
    assert "50x" in body["message"]
    assert "fapi" not in body["message"]


def test_the_adapter_never_shows_the_requested_value_as_the_actual_one(live) -> None:
    """Unchanged contract, re-asserted here because the capability path added a branch to it:
    what the screen shows after a change is the re-read, not the request."""
    client, adapter, fake, arm, *_ = live
    arm.arm(confirmation="ARM LIVE TRADING")

    result = adapter.set_leverage("10")

    assert result["requested"] == "10"
    assert result["leverage"] == Decimal(10)  # from the symbolConfig re-read, not the request


@pytest.fixture
def live(tmp_path: Path):
    """A TestClient over the real routes, with the fake transport. Mirrors the fixture in
    `test_binance_live_arm.py`; duplicated rather than imported so neither file's setup can
    drift the other's assertions."""
    import os

    from fastapi.testclient import TestClient

    from app.crypto.live.arm import ArmSession
    from app.crypto.live.mirror import LiveMirror, default_path
    from app.crypto.terminal.api import app
    from app.crypto.terminal.live_routes import live_runtime

    config = make_config(trading_enabled=True, client_armed=False)
    client_obj, fake = make_client(trading_enabled=False)
    fake.position_rows = POSITION_RISK_FLAT
    reader = AccountReader(client_obj, config)
    mirror = LiveMirror(path=default_path(config.fingerprint, tmp_path),
                        account_fingerprint=config.fingerprint)
    arm = ArmSession(client=client_obj, env_armed=False)
    capability = LeverageCapability(path=tmp_path / "leverage_capability.json")
    router_obj = LiveOrderRouter(reader=reader, client=client_obj, config=config, mirror=mirror,
                                 arm=arm, capability=capability)
    adapter = BinanceLiveAdapter(config=config, client=client_obj, reader=reader,
                                 router=router_obj, mirror=mirror)
    previous = (live_runtime.adapter, live_runtime.config, live_runtime.arm)
    live_runtime.adapter, live_runtime.config, live_runtime.arm = adapter, config, arm
    os.environ.setdefault("CRYPTO_LIVE_USER_STREAM", "off")
    try:
        with TestClient(app) as test_client:
            yield test_client, adapter, fake, arm, mirror
    finally:
        live_runtime.adapter, live_runtime.config, live_runtime.arm = previous


# ------------------------------------------------------------------ startup bootstrap
#
# The refusal is already in the audit mirror from the first time it happened. Making the
# operator press 50x once more after every deploy - sending a write Binance will reject again -
# is a worse screen than reading the answer the file already holds. These tests hold the line
# between recovering a known restriction and inventing one.

import json as _json

from app.crypto.live.leverage import (ALREADY_EXPIRED, ALREADY_PERSISTED, CONTRADICTED,
                                      FROM_EXCHANGE, FROM_MIRROR, NO_EVENT, NO_MIRROR, RECOVERED,
                                      UNREADABLE_UNLOCK, restriction_from_mirror)

#: 2026-10-01 01:38 UTC, when the refusal was actually recorded on the live account.
REFUSED_AT_MS = 1_790_854_704_365
#: Stands in for the real key fingerprint the mirror carries. Not a secret even there - it is a
#: one-way digest - but a fixture is no place for the operating account's identifier.
ACCOUNT_FINGERPRINT = "0a1b2c3d"
#: Comfortably before the unlock the message names.
BEFORE_UNLOCK_MS = LIFTS_AT_MS - 86_400_000


def mirror(tmp_path: Path, *rows: dict) -> Path:
    """An audit mirror holding exactly these lines, plus the ordinary traffic that surrounds
    them so the scan has to pick its two event types out of a real file."""
    path = tmp_path / "binance_live_events.jsonl"
    noise = [
        {"seq": 1, "ts_ms": 1, "event_type": "LIVE_DISARM", "reason": "BOOT"},
        {"seq": 2, "ts_ms": 2, "event_type": "LIVE_RECONCILE", "reason": "TTL"},
        {"seq": 3, "ts_ms": 3, "event_type": "LIVE_ORDER_RESULT", "response": {"symbol": SYMBOL}},
        {"seq": 4, "ts_ms": 4, "event_type": "LIVE_SNAPSHOT"},
    ]
    with path.open("w", encoding="utf-8") as stream:
        for row in [*noise, *rows]:
            stream.write(_json.dumps(row, ensure_ascii=False) + "\n")
    return path


def refusal(**over) -> dict:
    """The real line, as `orders.py` writes it."""
    return {"seq": 692, "ts_ms": REFUSED_AT_MS, "event_type": "LIVE_LEVERAGE_REFUSED",
            "account": ACCOUNT_FINGERPRINT, "stage": "EXCHANGE", "symbol": SYMBOL, "code": -4300,
            "status": 400, "requested": 50, "message": REFUSAL, **over}


def success(**over) -> dict:
    return {"seq": 697, "ts_ms": REFUSED_AT_MS + 6_000, "event_type": "LIVE_LEVERAGE_RESULT",
            "symbol": SYMBOL, "requested": 20,
            "response": {"leverage": 20, "maxNotionalValue": "100000000", "symbol": SYMBOL},
            **over}


def recovered(tmp_path: Path, *rows: dict, now_ms: int = BEFORE_UNLOCK_MS,
              symbol: str = SYMBOL):
    return restriction_from_mirror(mirror(tmp_path, *rows), symbol=symbol, now_ms=now_ms)


def test_a_known_restriction_is_recovered_from_the_audit_mirror(tmp_path: Path) -> None:
    outcome = recovered(tmp_path, refusal())

    assert outcome.reason == RECOVERED
    assert outcome.restriction is not None
    assert outcome.restriction.above == 20
    assert outcome.restriction.until_utc == "2026-10-29 01:34 UTC"
    # Points at when Binance actually said it, not at this startup.
    assert outcome.restriction.observed_at_ms == REFUSED_AT_MS
    assert outcome.restriction.source == FROM_MIRROR


def test_the_unlock_time_is_recovered_from_the_event_and_not_from_a_constant() -> None:
    """The date is Binance's, read out of the message. A literal in the code would be right
    today and silently wrong on the day it lifts, so there is not one.

    Only executable constants are examined. The module's own prose quotes the refusal verbatim,
    which is documentation of what was observed rather than a value anything branches on.
    """
    import ast

    tree = ast.parse(Path("backend/app/crypto/live/leverage.py").read_text(encoding="utf-8"))
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            first = body[0] if body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                prose.add(id(first.value))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in prose:
            continue
        if isinstance(node.value, str):
            assert "2026-10-29" not in node.value
            assert "01:34" not in node.value
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            assert node.value != LIFTS_AT_MS


def test_a_fresh_process_greys_the_steps_out_from_the_file_alone(tmp_path: Path) -> None:
    # The point of the whole path: after a restart, before anybody touches anything.
    capability = LeverageCapability(path=tmp_path / "leverage_capability.json")
    assert capability.unavailable(LADDER) == {}

    outcome = capability.bootstrap_from_mirror(mirror(tmp_path, refusal()), symbol=SYMBOL,
                                               now_ms=BEFORE_UNLOCK_MS)

    assert outcome.reason == RECOVERED
    assert set(capability.unavailable(LADDER, now_ms=BEFORE_UNLOCK_MS)) == {"50", "100"}
    assert capability.restriction_for(20, now_ms=BEFORE_UNLOCK_MS) is None


def test_the_bootstrap_is_persisted_so_it_survives_the_next_restart_too(tmp_path: Path) -> None:
    path = tmp_path / "leverage_capability.json"
    LeverageCapability(path=path).bootstrap_from_mirror(
        mirror(tmp_path, refusal()), symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)

    reloaded = LeverageCapability(path=path)
    assert set(reloaded.unavailable(LADDER, now_ms=BEFORE_UNLOCK_MS)) == {"50", "100"}


# ------------------------------------------------------------------ what it refuses to learn


def test_an_expired_restriction_is_not_restored(tmp_path: Path) -> None:
    # On 2026-10-29 the cap lifts; a process started after that must not grey anything out.
    outcome = recovered(tmp_path, refusal(), now_ms=LIFTS_AT_MS)
    assert outcome.reason == ALREADY_EXPIRED
    assert outcome.restriction is None


def test_the_ladder_reopens_by_itself_after_the_unlock_without_a_deploy(tmp_path: Path) -> None:
    path = tmp_path / "leverage_capability.json"
    audit = mirror(tmp_path, refusal())
    before = LeverageCapability(path=path)
    before.bootstrap_from_mirror(audit, symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)
    assert set(before.unavailable(LADDER, now_ms=BEFORE_UNLOCK_MS)) == {"50", "100"}

    # Same stored file, same audit file, one second past the moment Binance named.
    after = LeverageCapability(path=path)
    assert after.unavailable(LADDER, now_ms=LIFTS_AT_MS + 1_000) == {}
    assert after.bootstrap_from_mirror(audit, symbol=SYMBOL,
                                       now_ms=LIFTS_AT_MS + 1_000).reason == ALREADY_EXPIRED
    assert after.unavailable(LADDER, now_ms=LIFTS_AT_MS + 1_000) == {}


def test_a_refusal_about_another_symbol_is_not_this_symbols_restriction(tmp_path: Path) -> None:
    outcome = recovered(tmp_path, refusal(symbol="ETHUSDT"))
    assert outcome.reason == NO_EVENT
    assert outcome.restriction is None


def test_a_message_whose_unlock_time_cannot_be_read_is_not_restored(tmp_path: Path) -> None:
    """A live refusal is authoritative whatever it says; a line of unknown age with no deadline
    on it could be from any point in the past, and nothing in the file would say whether it
    still applies. Restoring it could grey out a working button indefinitely."""
    for broken in ("Leverage not available for this account.",
                   "You can start trading with more than 20x leverage soon.",
                   "more than 20x leverage by 2026-13-45 99:99 (UTC)", ""):
        outcome = recovered(tmp_path, refusal(message=broken))
        assert outcome.reason == UNREADABLE_UNLOCK, broken
        assert outcome.restriction is None


def test_an_invalid_leverage_refusal_teaches_nothing(tmp_path: Path) -> None:
    outcome = recovered(tmp_path, refusal(code=-4028, message="Leverage 50 is not valid"))
    assert outcome.reason == NO_EVENT


def test_a_position_held_refusal_teaches_nothing(tmp_path: Path) -> None:
    outcome = recovered(tmp_path, refusal(
        stage="POSITION", code="LEVERAGE_POSITION_OPEN", status=None,
        message="포지션 보유 중에는 레버리지를 변경할 수 없습니다."))
    assert outcome.reason == NO_EVENT


def test_a_timeout_or_a_server_error_teaches_nothing(tmp_path: Path) -> None:
    for row in (refusal(code=0, status=0, message="read timeout"),
                refusal(code=None, status=502, message="Bad Gateway"),
                refusal(code=-1001, status=500, message="Internal error; unable to process.")):
        assert recovered(tmp_path, row).reason == NO_EVENT


def test_a_transport_failure_teaches_nothing(tmp_path: Path) -> None:
    outcome = recovered(tmp_path, refusal(
        stage="EXCHANGE", code="ConnectError", status=None, message="connection refused"))
    assert outcome.reason == NO_EVENT


def test_a_gate_refusal_teaches_nothing(tmp_path: Path) -> None:
    # The most common refusal in the real file by far: the window simply was not armed.
    outcome = recovered(tmp_path, refusal(
        stage="GATE", code="LIVE_TRADING_DISABLED", status=None,
        message="레버리지 변경도 실계좌 쓰기입니다. 실주문이 잠겨 있습니다."))
    assert outcome.reason == NO_EVENT


def test_a_missing_or_damaged_mirror_is_simply_no_information(tmp_path: Path) -> None:
    assert restriction_from_mirror(tmp_path / "absent.jsonl", symbol=SYMBOL).reason == NO_MIRROR

    path = tmp_path / "binance_live_events.jsonl"
    path.write_text("{broken\nnot json at all\n", encoding="utf-8")
    outcome = restriction_from_mirror(path, symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)
    assert outcome.reason == NO_EVENT
    assert outcome.unreadable == 2


def test_a_damaged_line_does_not_hide_a_good_one(tmp_path: Path) -> None:
    path = tmp_path / "binance_live_events.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        stream.write("{broken\n")
        stream.write(_json.dumps(refusal()) + "\n")
        stream.write("also not json\n")
    outcome = restriction_from_mirror(path, symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)
    assert outcome.reason == RECOVERED
    assert outcome.unreadable == 2


# ------------------------------------------------------------------ precedence and freshness


def test_an_existing_valid_capability_file_is_not_second_guessed(tmp_path: Path) -> None:
    """The stored record is this process's own conclusion, kept current by every success since.
    The mirror is the raw material it was drawn from, so re-reading it could only reach the same
    answer or an older one."""
    path = tmp_path / "leverage_capability.json"
    capability = LeverageCapability(path=path)
    capability.note_refusal(leverage=50, code=-4300, message=REFUSAL)
    stored = _json.loads(path.read_text())

    # A mirror that would teach something *different* if it were consulted.
    other = refusal(ts_ms=REFUSED_AT_MS + 60_000, requested=10,
                    message=REFUSAL.replace("more than 20x", "more than 5x"))
    outcome = capability.bootstrap_from_mirror(mirror(tmp_path, other), symbol=SYMBOL,
                                               now_ms=BEFORE_UNLOCK_MS)

    assert outcome.reason == ALREADY_PERSISTED
    assert outcome.restriction is None
    assert _json.loads(path.read_text()) == stored
    assert capability.restriction_for(10, now_ms=BEFORE_UNLOCK_MS) is None
    assert capability._restrictions[0].source == FROM_EXCHANGE


def test_an_expired_stored_record_does_not_block_the_scan(tmp_path: Path) -> None:
    """Requirement 5 is about a capability file that is still *valid*. One whose restriction has
    lapsed holds no live answer, so the scan runs."""
    path = tmp_path / "leverage_capability.json"
    path.write_text(_json.dumps({"restrictions": [
        {"above": 20, "code": -4300, "until_ms": 1_000, "observed_at_ms": 1,
         "exchange_message": "old", "source": FROM_EXCHANGE}]}), encoding="utf-8")
    capability = LeverageCapability(path=path)

    outcome = capability.bootstrap_from_mirror(mirror(tmp_path, refusal()), symbol=SYMBOL,
                                               now_ms=BEFORE_UNLOCK_MS)

    assert outcome.reason == RECOVERED
    assert set(capability.unavailable(LADDER, now_ms=BEFORE_UNLOCK_MS)) == {"50", "100"}


def test_the_newest_refusal_wins(tmp_path: Path) -> None:
    older = refusal(ts_ms=REFUSED_AT_MS - 600_000,
                    message=REFUSAL.replace("more than 20x", "more than 5x"))
    outcome = recovered(tmp_path, older, refusal())
    assert outcome.restriction is not None
    assert outcome.restriction.above == 20


def test_a_later_success_above_the_threshold_retires_the_restriction(tmp_path: Path) -> None:
    """Binance lifted it early and the file simply has not been told in words. A success at 50x
    after the refusal is that telling."""
    lifted = success(ts_ms=REFUSED_AT_MS + 120_000, requested=50,
                     response={"leverage": 50, "maxNotionalValue": "300000", "symbol": SYMBOL})
    outcome = recovered(tmp_path, refusal(), lifted)
    assert outcome.reason == CONTRADICTED
    assert outcome.restriction is None


def test_a_later_success_below_the_threshold_changes_nothing(tmp_path: Path) -> None:
    # Exactly what happened on the real account: 50x refused, then 20x accepted.
    outcome = recovered(tmp_path, refusal(), success())
    assert outcome.reason == RECOVERED
    assert outcome.restriction is not None


def test_an_earlier_success_above_the_threshold_changes_nothing(tmp_path: Path) -> None:
    # Before the restriction existed. Ordering is the whole of the argument.
    earlier = success(ts_ms=REFUSED_AT_MS - 600_000, requested=50,
                      response={"leverage": 50, "symbol": SYMBOL})
    assert recovered(tmp_path, earlier, refusal()).reason == RECOVERED


def test_a_success_on_another_symbol_does_not_retire_this_symbols_restriction(tmp_path: Path) -> None:
    other = success(ts_ms=REFUSED_AT_MS + 120_000, requested=50, symbol="ETHUSDT",
                    response={"leverage": 50, "symbol": "ETHUSDT"})
    assert recovered(tmp_path, refusal(), other).reason == RECOVERED


def test_lines_written_before_the_symbol_field_existed_are_still_read(tmp_path: Path) -> None:
    """The real file's older leverage lines carry no `symbol`, and at the time they were written
    this package traded one. An unnamed line belongs to the configured symbol; a line naming a
    different one does not."""
    legacy = {k: v for k, v in refusal().items() if k != "symbol"}
    assert recovered(tmp_path, legacy).reason == RECOVERED


# ------------------------------------------------------------------ it is a read, and only a read


def test_the_bootstrap_makes_no_binance_request_at_all(tmp_path: Path) -> None:
    subject, fake = router(path=tmp_path / "leverage_capability.json")
    before = len(fake.calls)

    outcome = subject.capability.bootstrap_from_mirror(
        mirror(tmp_path, refusal()), symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)

    assert outcome.reason == RECOVERED
    assert fake.calls[before:] == []
    assert fake.count("/fapi/v1/leverage") == 0
    assert fake.count("/fapi/v1/order") == 0


def test_the_bootstrap_does_not_write_to_the_audit_mirror(tmp_path: Path) -> None:
    audit = mirror(tmp_path, refusal())
    before = audit.read_bytes()

    LeverageCapability(path=tmp_path / "leverage_capability.json").bootstrap_from_mirror(
        audit, symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)

    assert audit.read_bytes() == before


def test_a_recovered_restriction_refuses_the_press_without_reaching_binance(tmp_path: Path) -> None:
    """The whole point, end to end: a restarted process refuses 50x locally with the message
    Binance gave it, instead of sending the write again to be told the same thing."""
    subject, fake = router(path=tmp_path / "leverage_capability.json")
    subject.capability.bootstrap_from_mirror(mirror(tmp_path, refusal()), symbol=SYMBOL,
                                             now_ms=BEFORE_UNLOCK_MS)

    with pytest.raises(OrderRefused) as caught:
        subject.set_leverage(50)

    assert caught.value.code == ACCOUNT_LEVERAGE_NOT_YET_AVAILABLE
    assert "2026-10-29 01:34 UTC" in caught.value.message
    assert fake.count("/fapi/v1/leverage") == 0


def test_no_credential_reaches_the_bootstrapped_file(tmp_path: Path) -> None:
    path = tmp_path / "leverage_capability.json"
    LeverageCapability(path=path).bootstrap_from_mirror(
        mirror(tmp_path, refusal()), symbol=SYMBOL, now_ms=BEFORE_UNLOCK_MS)

    raw = path.read_text(encoding="utf-8")
    for leak in ("test-api-key-0123456789", "test-api-secret-abcdef", ACCOUNT_FINGERPRINT):
        assert leak not in raw
