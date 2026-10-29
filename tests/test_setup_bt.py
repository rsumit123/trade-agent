"""Setup backtester: data chunking/caching, setup rules, and the no-lookahead engine."""
import json
from datetime import date, datetime, timedelta

import pandas as pd

from agent.setup_bt import data as bt_data
from agent.setup_bt.engine import _vwap, daily_features, index_context, run_symbol, simulate_day
from agent.setup_bt.setups import Day, GapFade, OrbBreakout, VwapCross
from agent.trade_context import summarise

D = date(2026, 3, 2)


def _times(n, start=(9, 15)):
    t0 = datetime(D.year, D.month, D.day, *start)
    return [t0 + timedelta(minutes=5 * k) for k in range(n)]


def _day(closes, opens=None, highs=None, lows=None, vols=None, prev_close=100.0, atr=2.0):
    n = len(closes)
    opens = opens or [closes[0]] + closes[:-1]
    highs = highs or [max(o, c) + 0.1 for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) - 0.1 for o, c in zip(opens, closes)]
    vols = vols or [1000] * n
    return Day(times=_times(n), o=opens, h=highs, l=lows, c=closes, v=vols,
               vwap=_vwap(highs, lows, closes, vols), day_open=opens[0], prev_close=prev_close,
               atr=atr, prev_turnover_cr=50.0)


# ── data ───────────────────────────────────────────────────────────────────

def test_chunks_respect_kite_max_days():
    c = bt_data.chunks(date(2026, 1, 1), date(2026, 12, 31), 100)
    assert c[0] == (date(2026, 1, 1), date(2026, 4, 10)) and c[-1][1] == date(2026, 12, 31)
    assert all((b - a).days <= 99 for a, b in c)


def test_market_hours_guard():
    assert bt_data.in_market_hours(datetime(2026, 10, 29, 11, 0))
    assert not bt_data.in_market_hours(datetime(2026, 10, 29, 16, 0))
    assert not bt_data.in_market_hours(datetime(2026, 10, 31, 11, 0))      # Saturday


def test_fetch_is_resumable(tmp_path):
    calls = []

    def hist(token, f, t, iv):
        calls.append((f.date(), t.date()))
        days = pd.date_range(f.date(), t.date(), freq="D")
        return [{"date": datetime.combine(d.date(), datetime.min.time()).replace(hour=9, minute=15),
                 "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for d in days]

    n1 = bt_data.fetch_symbol(hist, 1, "AAA", date(2026, 1, 1), date(2026, 1, 10), tmp_path, throttle=0)
    n2 = bt_data.fetch_symbol(hist, 1, "AAA", date(2026, 1, 1), date(2026, 1, 15), tmp_path, throttle=0)
    assert (n1, n2) == (10, 5) and calls[-1] == (date(2026, 1, 11), date(2026, 1, 15))
    assert len(bt_data.load(tmp_path, "5minute", "AAA")) == 15


# ── setups ─────────────────────────────────────────────────────────────────

def test_gap_fade_fires_only_after_the_gap_fails():
    # Gap up 3% to 103, drifts up, then breaks below VWAP and the open.
    closes = [103.5, 104, 104.2, 103.8, 102.5, 102, 101.5] + [101.5] * 20
    d = _day(closes, opens=[103] + closes[:-1], prev_close=100)
    gf = GapFade()
    assert gf.signal(d, 3) is None                     # still above the open
    sig = gf.signal(d, 4)
    assert sig[0] == "short" and sig[1] == max(d.h[:5])
    small = _day(closes, opens=[101] + closes[:-1], prev_close=100)   # 1% gap: no setup
    assert gf.signal(small, 4) is None


def test_orb_needs_a_volume_backed_close_beyond_the_range():
    closes = [100, 100.5, 100.2, 100.4, 101.5] + [101.5] * 20
    vols = [1000, 1000, 1000, 1000, 3000] + [1000] * 20
    d = _day(closes, vols=vols)
    sig = OrbBreakout().signal(d, 4)
    assert sig == ("long", min(d.l[:3]))
    quiet = _day(closes, vols=[1000] * 25)
    assert OrbBreakout().signal(quiet, 4) is None


def test_vwap_cross_after_holding_one_side():
    closes = [100, 101, 101.5, 102, 102.2, 102.4, 102.5, 99.0] + [99] * 20
    d = _day(closes, atr=2.0)
    sig = VwapCross().signal(d, 7)
    assert sig[0] == "short" and abs(sig[1] - (d.vwap[7] + 1.0)) < 1e-9


# ── engine ─────────────────────────────────────────────────────────────────

class _Always:
    """Test setup: signal on the first eligible bar with a fixed stop distance."""
    name, target_r, min_stop_atr = "test", 2.0, 0.5

    def __init__(self, direction="long", stop_off=1.0):
        self.direction, self.stop_off = direction, stop_off

    def signal(self, d, i):
        off = -self.stop_off if self.direction == "long" else self.stop_off
        return self.direction, d.c[i] + off


def _engine_day(highs, lows, opens=None, n=75):
    closes = [100.0] * n
    opens = opens or [100.0] * n
    hs = highs + [100.1] * (n - len(highs))
    ls = lows + [99.9] * (n - len(lows))
    return Day(times=_times(n), o=opens, h=hs, l=ls, c=closes, v=[1000] * n,
               vwap=[100.0] * n, day_open=100.0, prev_close=100.0, atr=2.0, prev_turnover_cr=50.0)


def test_entry_is_next_bar_open_and_stop_wins_a_both_hit_bar():
    # Signal on bar 3 (09:30); entry bar 4 opens 100; bar 4 spans both stop (99) and target (102).
    highs = [100.1] * 4 + [103.0]
    lows = [99.9] * 4 + [98.0]
    d = _engine_day(highs, lows)
    (t,) = simulate_day(d, [_Always()], "X", {}, 10_000, 0.0, None)
    assert t["entry_time"].endswith("09:35:00") and t["entry_price"] == 100.0
    assert t["exit_type"] == "stop_hit" and t["exit_price"] == 99.0


def test_target_hit_and_slippage():
    highs = [100.1] * 6 + [103.0]
    d = _engine_day(highs, [99.9] * 7)
    (t,) = simulate_day(d, [_Always()], "X", {}, 10_000, 0.001, None)
    assert t["exit_type"] == "target_hit"
    assert t["entry_price"] == 100.1 and t["exit_price"] < t["target"]     # slippage both ways


def test_forced_close_at_1515_and_min_stop_widening():
    d = _engine_day([100.1] * 75, [99.9] * 75)
    (t,) = simulate_day(d, [_Always(stop_off=0.2)], "X", {}, 10_000, 0.0, None)
    assert t["exit_type"] == "forced_close" and t["exit_time"].endswith("15:15:00")
    assert t["stop"] == 99.0                                               # widened to 0.5 x ATR


def test_short_gapping_through_stop_fills_at_open():
    opens = [100.0] * 6 + [102.0]
    d = _engine_day([100.1] * 6 + [102.5], [99.9] * 7, opens=opens + [100.0] * 68)
    (t,) = simulate_day(d, [_Always("short")], "X", {}, 10_000, 0.0, None)
    assert t["exit_type"] == "stop_hit" and t["exit_price"] == 102.0


def test_run_symbol_uses_prior_days_only_and_feeds_summarise():
    rows = []
    for k in range(20):
        day = date(2026, 2, 2) + timedelta(days=k)
        base = 100 + k * 0.1
        for b in range(75):
            ts = datetime(day.year, day.month, day.day, 9, 15) + timedelta(minutes=5 * b)
            c = base + (0.5 if b == 4 else 0)
            rows.append({"date": ts, "open": base, "high": c + 0.2, "low": base - 0.2,
                         "close": c, "volume": 3000 if b == 4 else 1000})
    df = pd.DataFrame(rows)
    feats = daily_features(df)
    assert pd.isna(feats["atr"].iloc[13]) and not pd.isna(feats["atr"].iloc[14])   # 14 prior days needed
    trades = run_symbol(df, "X", [OrbBreakout()], {}, oos_from=date(2026, 2, 19))
    assert trades and {t["sample"] for t in trades} == {"in", "out"}
    ctx = json.loads(trades[0]["entry_context"])
    assert {"time_bucket", "gap_pct", "vs_vwap_atr", "liquidity"} <= set(ctx)
    rows = summarise(trades)
    assert rows[0]["setup"] == "orb_breakout" and rows[0]["n"] == len(trades)


def test_index_context_regime_inputs():
    ts = _times(3)
    nifty = pd.DataFrame({"date": ts, "open": [100, 100.2, 100.5], "high": [100.3, 100.6, 101],
                          "low": [99.9, 100.1, 100.4], "close": [100.2, 100.5, 101.0], "volume": 0})
    idx = index_context(nifty, None)
    assert idx[ts[2]]["nifty_chg_open_pct"] == 1.0 and idx[ts[2]]["nifty_range_pos"] == 100
