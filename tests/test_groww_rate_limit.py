"""Groww 429s: read-only calls ride them out, order calls are never retried."""
import pytest

import agent.groww_broker as gb


class _RateLimited(Exception):
    code = "429"


class _Client:
    def __init__(self, fail_times):
        self.fail, self.calls = fail_times, {}

    def _hit(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.calls[name] <= self.fail:
            raise _RateLimited("The rate limit for the Groww API has been exceeded")
        return {"ok": name}

    def get_smart_order_list(self, **kw):
        return self._hit("get_smart_order_list")

    def place_order(self, **kw):
        return self._hit("place_order")


def _broker(monkeypatch, fail):
    monkeypatch.setattr(gb.time, "sleep", lambda s: None)
    return gb.GrowwBroker(client=_Client(fail))


def test_read_call_retries_through_two_rate_limits(monkeypatch):
    b = _broker(monkeypatch, 2)
    assert b._call("get_smart_order_list") == {"ok": "get_smart_order_list"}
    assert b.client.calls["get_smart_order_list"] == 3


def test_read_call_gives_up_after_the_retries(monkeypatch):
    b = _broker(monkeypatch, 5)
    with pytest.raises(_RateLimited):
        b._call("get_smart_order_list")
    assert b.client.calls["get_smart_order_list"] == 3


def test_order_placement_is_never_retried(monkeypatch):
    b = _broker(monkeypatch, 1)
    with pytest.raises(_RateLimited):
        b._call("place_order")
    assert b.client.calls["place_order"] == 1
