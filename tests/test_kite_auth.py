import sys
import threading
import time
import types

import pytest

import agent.kite_auth as ka
from agent.kite_auth import KiteAuth


class _FakeKite:
    def __init__(self, api_key):
        self.token = None

    def set_access_token(self, token):
        self.token = token


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kiteconnect", types.SimpleNamespace(KiteConnect=_FakeKite))
    monkeypatch.setattr(ka, "LOGIN_RETRY_WAITS_S", [0, 0])
    return KiteAuth(api_key="k", token_dir=str(tmp_path))


def test_concurrent_agents_log_in_once(auth, tmp_path):
    # After a deploy every agent starts at once. Only one may log in; the rest
    # must reuse its token instead of clashing and falling back to yfinance.
    calls = []

    def slow_login(self):
        calls.append(1)
        time.sleep(0.2)
        return "tok"

    agents = [KiteAuth(api_key="k", token_dir=str(tmp_path)) for _ in range(4)]
    for a in agents:
        a._auto_login = slow_login.__get__(a)
    threads = [threading.Thread(target=a.get_authenticated_client) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert auth._load_cached_token()["access_token"] == "tok"


def test_login_retries_transient_failure(auth):
    attempts = iter([ValueError("Failed to extract request_token from Kite redirect"), "tok"])

    def flaky():
        r = next(attempts)
        if isinstance(r, Exception):
            raise r
        return r

    auth._auto_login = flaky
    auth.get_authenticated_client()
    assert auth._load_cached_token()["access_token"] == "tok"


def test_login_gives_up_after_retries(auth):
    n = []

    def always_fails():
        n.append(1)
        raise ValueError("Failed to extract request_token from Kite redirect")

    auth._auto_login = always_fails
    with pytest.raises(ValueError):
        auth.get_authenticated_client()
    assert len(n) == 3


def test_rejected_token_reuses_newer_cached_token(auth):
    # Each login revokes the previous token. An agent whose token was revoked
    # must pick up the newer cached one, not log in again and revoke it.
    auth._cache_token("newer")
    auth._auto_login = lambda: pytest.fail("should not log in")
    assert auth.refresh_client(rejected_token="older").token == "newer"


def test_rejected_cached_token_triggers_login(auth):
    auth._cache_token("bad")
    auth._auto_login = lambda: "fresh"
    assert auth.refresh_client(rejected_token="bad").token == "fresh"
    assert auth._load_cached_token()["access_token"] == "fresh"
