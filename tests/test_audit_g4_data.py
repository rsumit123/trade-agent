"""Audit fixes for the Kite/market data layer (g4-data)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import agent.kite_data as kd
import agent.market_data as md_mod
from agent.kite_data import KiteMarketData, compute_technicals, macd_state_from
from agent.market_presets import get_preset

from tests.test_watchlist_indicators import FakeKite, QUOTE, _daily

IST = ZoneInfo("Asia/Kolkata")


def _df(n, vol=1000):
    rows = [{"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": vol} for i in range(n)]
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(kd, "DAILY_CACHE_DIR", tmp_path / "daily")
    monkeypatch.setattr(kd, "HIST_THROTTLE_S", 0)
    monkeypatch.setattr(kd.time, "sleep", lambda s: None)


# 1. missing / short history -> res/sup n/a, not 0.0%
@pytest.mark.parametrize("n", [0, 1])
def test_no_past_history_gives_none_not_zero(n):
    t = compute_technicals(_df(n), 100.0, 500)
    assert t["dist_to_resistance_pct"] is None and t["dist_to_support_pct"] is None
    assert t["high_5d"] is None and t["low_5d"] is None


def test_short_history_uses_available_bars():
    t = compute_technicals(_df(6), 105.0, 500)   # 5 past bars + today, < 15
    assert t["high_5d"] == 106.0 and t["low_5d"] == 100.0
    assert t["dist_to_resistance_pct"] == round((106 - 105) / 105 * 100, 2)
    assert t["rsi_14"] is None


def test_empty_frame_without_columns_does_not_crash():
    t = compute_technicals(pd.DataFrame(), 100.0, 0)
    assert t["dist_to_resistance_pct"] is None and t["avg_volume_20d"] is None


# 2. macd_state honesty
def test_macd_state_distinguishes_state_from_cross():
    s = pd.Series
    assert macd_state_from(s([-1, -0.5, 0.2])) == "bullish_cross"
    assert macd_state_from(s([-1, 0.2, 0.5])) == "bullish_cross_1bar_ago"
    assert macd_state_from(s([0.3, 0.2, 0.5, 0.6])) == "above_signal"
    assert macd_state_from(s([1, 0.5, -0.2])) == "bearish_cross"
    assert macd_state_from(s([1, -0.2, -0.5])) == "bearish_cross_1bar_ago"
    assert macd_state_from(s([-0.3, -0.2, -0.5])) == "below_signal"
    assert macd_state_from(s([1, 2])) is None


def test_steady_uptrend_is_not_reported_as_a_cross():
    closes = [100 + i for i in range(80)]
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1] * 80})
    assert compute_technicals(df, 179.0, 1)["macd_state"] in ("above_signal", "neutral")


# 3/5/6. get_stock_context error shape, volumes, price_vs_sma
def _md(quotes, history=None):
    kite = FakeKite(history or {"AAA": _daily(70)}, quotes)
    return KiteMarketData(["AAA.NS"], kite_client=kite)


def test_unknown_ticker_returns_error():
    ctx = _md({}).get_stock_context("ZZZ.NS")
    assert "error" in ctx and "current_price" not in ctx


def test_exception_returns_error():
    m = _md({"NSE:AAA": QUOTE})

    def boom(s):
        raise RuntimeError("boom")
    m.kite.quote = boom
    assert m.get_stock_context("AAA.NS") == {"ticker": "AAA.NS", "error": "boom"}


def test_volume_fields_are_honest():
    ctx = _md({"NSE:AAA": QUOTE}).get_stock_context("AAA.NS")
    assert "avg_volume" not in ctx
    assert ctx["today_volume"] == 3000
    assert ctx["avg_volume_20d"] == 1000      # past bars only; today's 3000 excluded


def test_price_vs_sma_uses_sma5_not_vwap():
    # price 170 is below VWAP 175 but above sma_5 (~166): must be "above"
    q = {**QUOTE, "average_price": 175.0}
    m = _md({"NSE:AAA": q})
    row = m.get_watchlist_summary()[0]
    assert row["price_vs_sma"] == "above" and row["vwap_dist_pct"] < 0
    assert m.get_stock_context("AAA.NS")["price_vs_sma"] == "above"


# 4. instrument list retry
class FlakyInstruments(FakeKite):
    def __init__(self, *a, fails=0, **k):
        super().__init__(*a, **k)
        self.fails, self.calls = fails, 0

    def instruments(self, exchange):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("down")
        return super().instruments(exchange)


def test_startup_retries_instrument_load():
    k = FlakyInstruments({"AAA": _daily(3)}, {}, fails=2)
    m = KiteMarketData(["AAA.NS"], kite_client=k)
    assert k.calls == 3 and m._get_instrument_token("AAA.NS") == 1


def test_empty_map_lazily_reloads_rate_limited():
    k = FlakyInstruments({"AAA": _daily(3)}, {}, fails=3)
    m = KiteMarketData(["AAA.NS"], kite_client=k)
    assert k.calls == 3 and not m._instrument_map
    assert m._get_instrument_token("AAA.NS") is None and k.calls == 3     # rate limited
    m._instruments_tried_at -= kd.INSTRUMENT_RELOAD_MIN_S + 1
    assert m._get_instrument_token("AAA.NS") == 1 and k.calls == 4


# 7. news query
def test_intraday_news_query_has_no_placeholder():
    assert all("{" not in q for q in get_preset("nse-intraday").news_queries)


# 8. holiday detection
class HolidayKite(FakeKite):
    def __init__(self, candles, err=None):
        super().__init__({"AAA": _daily(3)}, {})
        self.candles, self.err, self.probes = candles, err, []

    def historical_data(self, instrument_token, from_date, to_date, interval):
        self.probes.append((instrument_token, from_date, to_date, interval))
        if self.err:
            raise self.err
        return self.candles


def _open_md(kite, monkeypatch, hhmm="11:00", day=(2026, 10, 14)):
    m = KiteMarketData(["AAA.NS"], market_preset=get_preset("nse"), kite_client=kite)
    h, mi = map(int, hhmm.split(":"))
    fake = datetime(*day, h, mi, tzinfo=IST)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake.astimezone(tz) if tz else fake
    monkeypatch.setattr(kd, "datetime", FakeDT)
    return m


def test_holiday_when_nifty_has_no_candles(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(kd.time, "monotonic", lambda: clock["t"])
    k = HolidayKite([])
    m = _open_md(k, monkeypatch, day=(2026, 10, 2))
    assert m.is_market_open() is True                     # one empty probe could be late data
    clock["t"] += 300
    assert m.is_market_open() is True and len(k.probes) == 1
    clock["t"] += 300                                     # confirmed 10 min later
    assert m.is_market_open() is False and len(k.probes) == 2
    assert m.is_market_open() is False and len(k.probes) == 2
    assert k.probes[0] == (256265, "2026-10-02", "2026-10-02", "minute")


def test_late_candles_undo_a_holiday(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(kd.time, "monotonic", lambda: clock["t"])
    k = HolidayKite([])
    m = _open_md(k, monkeypatch)
    m.is_market_open(); clock["t"] += 600
    assert m.is_market_open() is False
    k.candles = [{"date": 1}]
    clock["t"] += 1799
    assert m.is_market_open() is False                    # not re-checked yet
    clock["t"] += 1
    assert m.is_market_open() is True and m.is_market_open() is True and len(k.probes) == 3


def test_trading_day_with_candles_is_open(monkeypatch):
    k = HolidayKite([{"date": 1}])
    m = _open_md(k, monkeypatch)
    assert m.is_market_open() is True and m.is_market_open() is True and len(k.probes) == 1


def test_probe_error_fails_open_and_is_rate_limited(monkeypatch):
    k = HolidayKite([], err=RuntimeError("kite down"))
    m = _open_md(k, monkeypatch)
    assert m.is_market_open() is True and m.is_market_open() is True
    assert len(k.probes) == 1 and m._holiday is None


def test_no_probe_before_0920(monkeypatch):
    k = HolidayKite([])
    m = _open_md(k, monkeypatch, hhmm="09:17")
    assert m.is_market_open() is True and k.probes == []


def test_holiday_probe_uses_ist_date_not_host_date(monkeypatch):
    # 09:30 IST on the 14th is still the 13th in UTC
    k = HolidayKite([{"date": 1}])
    m = _open_md(k, monkeypatch, hhmm="09:30")
    assert m.is_market_open() is True and k.probes[0][1] == "2026-10-14"


# 9. Kite init retry + alert
def _cfg(source="kite"):
    return SimpleNamespace(data_source=source, watchlist=["AAA.NS"],
                           _session_config=SimpleNamespace(data_source=source, session_id="nse_x"))


def test_kite_init_retries_then_alerts_and_falls_back(monkeypatch):
    import agent.kite_auth as ka
    import agent.health as health
    attempts, sent, slept = [], [], []

    class BadAuth:
        def __init__(self):
            attempts.append(1)

        def get_authenticated_client(self):
            raise RuntimeError("login down")
    monkeypatch.setattr(ka, "KiteAuth", BadAuth)
    monkeypatch.setattr(health, "send_telegram", lambda t: sent.append(t) or True)
    monkeypatch.setattr(md_mod.time, "sleep", slept.append)
    out = md_mod.create_market_data(_cfg())
    assert isinstance(out, md_mod.MarketData) and len(attempts) == 3 and len(slept) == 2
    assert len(sent) == 1 and "nse_x" in sent[0] and "login down" in sent[0]


def test_kite_init_recovers_on_retry(monkeypatch):
    import agent.kite_auth as ka
    import agent.health as health
    n, sent = [], []

    class FlakyAuth:
        def get_authenticated_client(self):
            n.append(1)
            if len(n) < 2:
                raise RuntimeError("transient")
            return FakeKite({"AAA": _daily(3)}, {})
    monkeypatch.setattr(ka, "KiteAuth", FlakyAuth)
    monkeypatch.setattr(health, "send_telegram", lambda t: sent.append(t))
    monkeypatch.setattr(md_mod.time, "sleep", lambda s: None)
    assert isinstance(md_mod.create_market_data(_cfg()), KiteMarketData) and not sent


def test_api_path_tries_kite_once_without_sleeping_or_alerting(monkeypatch):
    import agent.kite_auth as ka
    import agent.health as health
    attempts, sent = [], []

    class BadAuth:
        def __init__(self):
            attempts.append(1)

        def get_authenticated_client(self):
            raise RuntimeError("login failed")
    monkeypatch.setattr(ka, "KiteAuth", BadAuth)
    monkeypatch.setattr(health, "send_telegram", lambda text: sent.append(text))
    monkeypatch.setattr(md_mod.time, "sleep", lambda s: pytest.fail("slept on the request path"))
    assert isinstance(md_mod.create_market_data(_cfg(), retry_kite=False), md_mod.MarketData)
    assert attempts == [1] and sent == []
