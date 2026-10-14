"""The live market block NSE agents see. Numbers here go straight into trading
decisions, so each one is pinned against hand-computed values."""
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import agent.market_context as mc
from agent.decision_engine import _build_context

# 2026-10-14 close, as Kite quoted it (verified live against Kite's net_change
# and the day's candles).
QUOTES = {
    "NSE:NIFTY 50": {"last_price": 22622.7, "instrument_token": 256265,
                     "ohlc": {"open": 22715.75, "high": 22757.95, "low": 22558.95, "close": 22721.95}},
    "NSE:NIFTY BANK": {"last_price": 55316.0, "instrument_token": 260105,
                       "ohlc": {"open": 55406.7, "high": 55644.7, "low": 55168.2, "close": 55481.05}},
    "NSE:INDIA VIX": {"last_price": 14.63, "instrument_token": 264969,
                      "ohlc": {"open": 14.12, "high": 15.02, "low": 13.8, "close": 14.12}},
    # breadth: A up on the day and since open, B down on both, C up on the day but down since open
    "NSE:A": {"last_price": 110, "ohlc": {"open": 105, "close": 100}},
    "NSE:B": {"last_price": 90, "ohlc": {"open": 95, "close": 100}},
    "NSE:C": {"last_price": 101, "ohlc": {"open": 103, "close": 100}},
}


class FakeMD:
    def __init__(self, quotes=QUOTES, candles=None):
        self.quotes, self.candles, self.hist_args = quotes, candles or [], None

    def _kite_symbol(self, t):
        return "NSE:" + t.replace(".NS", "")

    def _call_with_retry(self, label, fn):
        return fn(self)

    def quote(self, syms):
        return {s: self.quotes[s] for s in syms if s in self.quotes}

    def historical_data(self, token, start, end, interval):
        self.hist_args = (token, start, end, interval)
        return self.candles


def candles(start_h, start_m, opens):
    t0 = datetime(2026, 10, 14, start_h, start_m)
    return [{"date": t0 + timedelta(minutes=5 * i), "open": o} for i, o in enumerate(opens)]


def test_index_numbers_match_kite():
    ctx = mc.fetch(FakeMD(), ["A.NS", "B.NS", "C.NS"], now=datetime(2026, 10, 14, 15, 0))
    nifty, bank, vix = ctx["indices"]
    assert (nifty["chg_prev_pct"], nifty["chg_open_pct"], nifty["range_pos_pct"]) == (-0.44, -0.41, 32)
    assert (bank["chg_prev_pct"], bank["chg_open_pct"]) == (-0.3, -0.16)
    assert vix["chg_prev_pct"] == 3.61
    assert ctx["nifty_chg_open_pct"] == -0.41


def test_breadth_counts_vs_prev_close_and_vs_open():
    ctx = mc.fetch(FakeMD(), ["A.NS", "B.NS", "C.NS"], now=datetime(2026, 10, 14, 15, 0))
    assert ctx["breadth"] == {"up_prev": 2, "down_prev": 1, "up_open": 1, "down_open": 2}


def test_trend_uses_the_last_30_minutes_in_ist():
    # 5-min candles 12:30..13:00; the 30-min base is the 12:30 candle's open.
    md = FakeMD(candles=candles(12, 30, [22725.4, 22700, 22690, 22680, 22650, 22620, 22600]))
    ctx = mc.fetch(md, [], now=datetime(2026, 10, 14, 13, 5))
    assert ctx["indices"][0]["trend_pct"] == round((22622.7 - 22725.4) / 22725.4 * 100, 2)
    # Kite reads naive datetimes as IST, so the window must be IST wall-clock time.
    assert md.hist_args[1:] == (datetime(2026, 10, 14, 12, 25), datetime(2026, 10, 14, 13, 5), "5minute")


def test_default_window_is_ist_even_on_a_utc_host(monkeypatch):
    md = FakeMD(candles=candles(9, 30, [22700]))
    mc.fetch(md, [])
    end = md.hist_args[2]
    ist_now = datetime.now(mc.IST).replace(tzinfo=None)
    assert abs((ist_now - end).total_seconds()) < 60


def test_no_block_before_the_open_or_on_errors():
    preopen = {k: {**v, "ohlc": {**v["ohlc"], "open": 0}} for k, v in QUOTES.items()}
    assert mc.fetch(FakeMD(quotes=preopen), []) is None

    class Broken(FakeMD):
        def quote(self, syms):
            raise RuntimeError("kite down")
    assert mc.fetch(Broken(), []) is None
    assert mc.format_context(None) == ""


def test_relative_strength_is_move_since_open_minus_niftys():
    rows = [{"ticker": "SHRIRAMFIN.NS", "chg_open_pct": -3.7}, {"ticker": "X.NS", "chg_open_pct": None}]
    mc.add_relative_strength(rows, {"nifty_chg_open_pct": -0.41})
    assert rows[0]["rs_vs_nifty"] == -3.29
    assert "rs_vs_nifty" not in rows[1]


def test_block_and_vsnifty_column_reach_the_prompt():
    ctx = mc.fetch(FakeMD(candles=candles(12, 30, [22725.4])), ["A.NS", "B.NS", "C.NS"],
                   now=datetime(2026, 10, 14, 13, 5))
    block = mc.format_context(ctx)
    assert "NIFTY 50    22,622.70 · vs prev close -0.44% · since today's open -0.41% · last 30 min -0.45%" in block
    assert "INDIA VIX   14.63 (+3.61% vs prev close" in block
    assert "Breadth (Nifty 50 stocks): 2 up / 1 down vs prev close · 1 up / 2 down since today's open" in block

    row = {"ticker": "TRF.NS", "current_price": 230.0, "change_pct": 1.7, "price_vs_sma": "below",
           "chg_open_pct": 1.82, "rs_vs_nifty": 2.23}
    text = _build_context({"cash": 1}, [row], "news", {}, NS(currency_symbol="₹", _market_preset=None),
                          market_context=block)
    assert block in text and text.index(block) < text.index("## Full Watchlist")
    assert "vsNifty= +2.23%" in text
    assert "## Market Now" not in _build_context({"cash": 1}, [row], "news", {},
                                                  NS(currency_symbol="₹", _market_preset=None))
