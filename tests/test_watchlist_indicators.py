"""Watchlist rows carry real indicators (they used to be n/a on Kite), computed
from the same shared daily history as get_stock_details, so both agree."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import agent.kite_data as kd
from agent.kite_data import KiteMarketData, _with_today_bar, compute_technicals

TODAY = datetime.now(kd.IST).date()


def _daily(n, start=100.0, step=1.0, vol=1000):
    """n past daily candles ending yesterday, closes rising by `step`."""
    rows = []
    for i in range(n):
        d = TODAY - timedelta(days=n - i)
        c = start + i * step
        rows.append({"date": datetime(d.year, d.month, d.day), "open": c - 0.5, "high": c + 1,
                     "low": c - 1, "close": c, "volume": vol})
    return rows


class FakeKite:
    def __init__(self, history, quotes, fail=()):
        self.history, self.quotes, self.fail = history, quotes, set(fail)
        self.hist_calls = []

    def instruments(self, exchange):
        return [{"tradingsymbol": s, "instrument_token": i} for i, s in enumerate(self.history, start=1)]

    def historical_data(self, instrument_token, from_date, to_date, interval):
        sym = list(self.history)[instrument_token - 1]
        self.hist_calls.append((sym, from_date, to_date, interval))
        if sym in self.fail:
            raise RuntimeError("Too many requests")
        # Kite would include today's partial candle if asked; we must drop it.
        return self.history[sym] + [{"date": datetime.combine(TODAY, datetime.min.time()), "open": 1,
                                     "high": 1, "low": 1, "close": 1, "volume": 1}]

    def quote(self, syms):
        return {s: self.quotes[s] for s in syms if s in self.quotes}


QUOTE = {"last_price": 170.0, "volume": 3000, "average_price": 168.0,
         "ohlc": {"open": 165.0, "high": 172.0, "low": 160.0, "close": 169.0}}


@pytest.fixture
def md(tmp_path, monkeypatch):
    monkeypatch.setattr(kd, "DAILY_CACHE_DIR", tmp_path / "daily")
    monkeypatch.setattr(kd, "HIST_THROTTLE_S", 0)
    kite = FakeKite({"AAA": _daily(70), "BBB": _daily(70, start=50, step=-0.2)},
                    {"NSE:AAA": QUOTE, "NSE:BBB": {**QUOTE, "last_price": 36.0, "average_price": 37.0,
                                                    "ohlc": {"open": 36.5, "high": 37.5, "low": 35.5, "close": 36.2}}})
    return KiteMarketData(["AAA.NS", "BBB.NS"], kite_client=kite)


def test_today_bar_comes_from_the_live_quote():
    past = pd.DataFrame(_daily(3)).drop(columns="date")
    df = _with_today_bar(past, QUOTE)
    assert df.iloc[-1].to_dict() == {"open": 165.0, "high": 172.0, "low": 160.0, "close": 170.0, "volume": 3000}
    # pre-open (no open yet): no today bar
    assert len(_with_today_bar(past, {**QUOTE, "ohlc": {"open": 0}})) == 3


def test_indicators_hand_checked():
    df = pd.DataFrame(_daily(30, start=100, step=1))      # closes 100..129, highs +1, lows -1
    t = compute_technicals(df, 129.0, 2000)
    assert t["rsi_14"] == 100.0                            # only up days
    assert t["sma_5"] == 127.0                             # mean(125..129)
    assert (t["high_5d"], t["low_5d"]) == (130.0, 124.0)
    assert t["vol_ratio"] == 2.0                           # 2000 / 1000
    assert t["dist_to_resistance_pct"] == round(1 / 129 * 100, 2)
    assert t["atr_14"] is not None and t["ema_20"] is not None and t["ema_50"] is None
    falling = compute_technicals(pd.DataFrame(_daily(30, step=-1)), 71.0, 1000)
    assert falling["rsi_14"] == 0.0


def test_history_is_fetched_once_a_day_and_shared(md, tmp_path):
    md.get_watchlist_summary()
    calls = list(md.kite.hist_calls)
    assert sorted(c[0] for c in calls) == ["AAA", "BBB"]
    assert all(c[2] == (TODAY - timedelta(days=1)).isoformat() and c[3] == "day" for c in calls)
    md.get_watchlist_summary()                              # same agent, next cycle: memory
    other = KiteMarketData(["AAA.NS"], kite_client=md.kite)   # another agent: shared file
    other.get_watchlist_summary()
    assert md.kite.hist_calls == calls
    # today's partial candle from Kite is never stored as history
    assert TODAY.isoformat() not in (kd.DAILY_CACHE_DIR / f"{TODAY.isoformat()}.json").read_text()


def test_rows_carry_indicators_and_intraday_position(md):
    rows = {r["ticker"]: r for r in md.get_watchlist_summary()}
    a = rows["AAA.NS"]
    assert a["rsi_14"] is not None and a["vol_ratio"] is not None and a["atr_14"] is not None
    assert a["vwap_dist_pct"] == round((170 - 168) / 168 * 100, 2)
    assert a["from_day_high_pct"] == round((170 - 172) / 172 * 100, 2)
    assert a["from_day_low_pct"] == round((170 - 160) / 160 * 100, 2)


def test_watchlist_row_matches_get_stock_details(md):
    rows = {r["ticker"]: r for r in md.get_watchlist_summary()}
    for t in ("AAA.NS", "BBB.NS"):
        d = md.get_stock_context(t)
        for k in ("rsi_14", "atr_14", "vol_ratio", "dist_to_resistance_pct", "dist_to_support_pct", "sma_5"):
            assert rows[t][k] == d[k], (t, k)


def test_failed_history_shows_na_and_is_retried(md):
    md.kite.fail = {"BBB"}
    rows = {r["ticker"]: r for r in md.get_watchlist_summary()}
    assert rows["BBB.NS"]["rsi_14"] is None and rows["BBB.NS"]["current_price"] == 36.0
    assert rows["AAA.NS"]["rsi_14"] is not None
    md.kite.fail = set()
    rows = {r["ticker"]: r for r in md.get_watchlist_summary()}
    assert rows["BBB.NS"]["rsi_14"] is not None


def test_specific_price_request_never_returns_stale_cache(md):
    assert md.get_current_prices() == {"AAA.NS": 170.0, "BBB.NS": 36.0}   # fills the watchlist cache

    def boom(syms):
        raise ConnectionResetError("Connection reset by peer")
    md.kite.quote = boom
    assert md.get_current_prices(["AAA.NS"]) == {}           # stop monitor: no stale price
    assert md.get_current_prices() == {"AAA.NS": 170.0, "BBB.NS": 36.0}
