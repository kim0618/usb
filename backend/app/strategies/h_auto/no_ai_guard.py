"""PRODUCTION_AI_CALLS = 0, enforced at runtime rather than promised.

H's production evaluation is code only (``H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI``). The research-time D3
and D4 runners still exist in the repository for historical reproducibility, and they still know how
to start the Claude CLI. ``forbid_ai()`` wraps every production evaluation so that none of the ways an
AI call could leave this process can succeed while it is active:

* a subprocess whose executable is a Claude CLI (``claude`` basename, or the path in
  ``CLAUDE_CODE_EXECPATH``), whatever module starts it;
* the research runners' own model entry points (``call_opus``, ``research_one_v3``,
  ``analyze_one_v2``), replaced by a function that raises;
* importing an LLM client package (``anthropic``, ``openai``, ...);
* resolving or connecting to an LLM API host.

A blocked attempt raises ``ProductionAICallBlocked`` and is counted. The executor turns it into a
FAILED queue item; nothing is written to the forward ledger. ``CLAUDE_CODE_EXECPATH`` is removed from
the environment for the duration, so a child process cannot inherit it either.
"""

from __future__ import annotations

import builtins
import importlib
import os
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LLM_PACKAGES = ("anthropic", "openai", "claude_agent_sdk", "claude_code_sdk", "google.generativeai",
                "google.genai", "mistralai", "cohere", "litellm", "langchain_openai", "langchain_anthropic")
LLM_HOSTS = ("api.anthropic.com", "anthropic.com", "claude.ai", "api.openai.com", "openai.com",
             "generativelanguage.googleapis.com", "api.mistral.ai", "api.cohere.ai", "api.cohere.com")
#: (module, attribute) of every research-time model entry point. Patched to raise while guarded.
MODEL_ENTRY_POINTS = (
    ("app.dev.run_strategy_h_v2_d3_3", "call_opus"),
    ("app.dev.run_strategy_h_v2_d3_3", "research_one_v3"),
    ("app.dev.run_strategy_h_v2_d4_2", "call_opus"),
    ("app.dev.run_strategy_h_v2_d4_2", "analyze_one_v2"),
)


class ProductionAICallBlocked(RuntimeError):
    pass


class _Ledger:
    def __init__(self) -> None:
        self.blocked: list[str] = []

    def block(self, what: str) -> None:
        self.blocked.append(what)
        raise ProductionAICallBlocked(f"PRODUCTION_AI_CALL_BLOCKED: {what}")


def _is_claude(argv: Any, execpath: str | None) -> bool:
    if isinstance(argv, (str, bytes, os.PathLike)):
        first = os.fsdecode(argv).split()[0] if os.fsdecode(argv).strip() else ""
    else:
        items = list(argv or [])
        first = os.fsdecode(items[0]) if items else ""
    name = Path(first).name.lower()
    return name in ("claude", "claude.exe") or name.startswith("claude-code") or \
        bool(execpath and first and os.path.realpath(first) == os.path.realpath(execpath))


def _llm_host(host: Any) -> bool:
    if not isinstance(host, (str, bytes)):
        return False
    name = (host.decode() if isinstance(host, bytes) else host).lower().rstrip(".")
    return any(name == h or name.endswith("." + h) for h in LLM_HOSTS)


@contextmanager
def forbid_ai() -> Iterator[_Ledger]:
    ledger = _Ledger()
    execpath = os.environ.pop("CLAUDE_CODE_EXECPATH", None)
    original_popen = subprocess.Popen
    original_import = builtins.__import__
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    patched: list[tuple[Any, str, Any]] = []

    class GuardedPopen(original_popen):                      # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            if _is_claude(args, execpath):
                ledger.block(f"subprocess {args!r}")
            super().__init__(args, *a, **kw)

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and any(name == p or name.startswith(p + ".") for p in LLM_PACKAGES):
            ledger.block(f"import {name}")
        return original_import(name, globals, locals, fromlist, level)

    def guarded_getaddrinfo(host, *a, **kw):
        if _llm_host(host):
            ledger.block(f"resolve {host}")
        return original_getaddrinfo(host, *a, **kw)

    def guarded_connect(self, address):
        if isinstance(address, tuple) and address and _llm_host(address[0]):
            ledger.block(f"connect {address[0]}")
        return original_connect(self, address)

    def refuse(label: str):
        def _refuse(*_a, **_kw):
            ledger.block(label)
        return _refuse

    subprocess.Popen = GuardedPopen                          # type: ignore[misc]
    builtins.__import__ = guarded_import
    socket.getaddrinfo = guarded_getaddrinfo                 # type: ignore[assignment]
    socket.socket.connect = guarded_connect                  # type: ignore[method-assign]
    try:
        for module_name, attr in MODEL_ENTRY_POINTS:
            module = importlib.import_module(module_name)
            patched.append((module, attr, getattr(module, attr)))
            setattr(module, attr, refuse(f"{module_name}.{attr}"))
        yield ledger
    finally:
        for module, attr, value in reversed(patched):
            setattr(module, attr, value)
        socket.socket.connect = original_connect             # type: ignore[method-assign]
        socket.getaddrinfo = original_getaddrinfo            # type: ignore[assignment]
        builtins.__import__ = original_import
        subprocess.Popen = original_popen                    # type: ignore[misc]
        if execpath is not None:
            os.environ["CLAUDE_CODE_EXECPATH"] = execpath
