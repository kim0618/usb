"""Local sandbox harness: selector wakeups and a 30-second deadline per test.

An empty FastAPI TestClient also hangs here without scheduled wakeups. No app code, exchange
mock, assertion or fsync is patched. The deadline exposes other environment stalls as failures.
"""
import asyncio
import signal
import sys
import pytest
original_run_forever=asyncio.BaseEventLoop.run_forever

def run_forever(loop):
    handle=None
    def pulse():
        nonlocal handle
        handle=loop.call_later(.01,pulse)
    pulse()
    try:return original_run_forever(loop)
    finally:
        if handle is not None:handle.cancel()
asyncio.BaseEventLoop.run_forever=run_forever

class Deadline:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self,item,nextitem):
        def expired(signum,frame):raise TimeoutError('Local audit harness: test exceeded 30 seconds')
        previous=signal.signal(signal.SIGALRM,expired)
        signal.setitimer(signal.ITIMER_REAL,30)
        try:yield
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous)
raise SystemExit(pytest.main(sys.argv[1:],plugins=[Deadline()]))
