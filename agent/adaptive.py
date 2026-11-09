"""Market-regime calibration of the numeric entry thresholds.

The live agent had written two hard numeric rules for itself after single losses
(N=1): "entry at most 0.35 ATR from VWAP" and "at least 1.6R". On a steady
trending market every candidate fails one of them, so it sat out 8 sessions in
which the average Nifty 50 stock gained +6.4% open to close. Fixed numbers
cannot suit every regime, so the harness now computes them each cycle from live
index data and injects them (session setting adaptive_rules). Learned rules
keep describing patterns; the numbers come from here.

Initial anchors (to be tuned from per-regime results of the tagged trades and
the backtester; every cycle's regime is logged):

  trend day, trade WITH the trend:   min reward:risk 1.2, up to 0.7 daily-ATR from VWAP
  range day, or against the trend:   min reward:risk 1.6, up to 0.35 daily-ATR from VWAP (as before)

Pure functions; nothing here may raise into the trading loop.
"""

from typing import Dict, Optional

from .trade_context import regime

TREND_RR, RANGE_RR = 1.2, 1.6
TREND_VWAP_ATR, RANGE_VWAP_ATR = 0.7, 0.35
# Hard bounds for any future automatic tuning of the anchors.
RR_BOUNDS, VWAP_ATR_BOUNDS = (1.0, 1.8), (0.3, 1.0)


def calibrate(market: Optional[Dict]) -> Optional[Dict]:
    """Regime and thresholds from market_context.fetch() output; None without index data."""
    try:
        if not market:
            return None
        nifty = next((i for i in market.get("indices", []) if i.get("name") == "NIFTY 50"), None)
        if not nifty or nifty.get("chg_open_pct") is None:
            return None
        chg, pos = nifty["chg_open_pct"], nifty.get("range_pos_pct")
        b = market.get("breadth") or {}
        up, down = b.get("up_open") or 0, b.get("down_open") or 0
        share = up / (up + down) if up + down else 0.5
        reg = regime(chg, pos)
        # A trend needs the breadth to agree: a few heavyweights moving the index is not a trend day.
        if reg == "trend_up" and share < 0.55:
            reg = "range"
        if reg == "trend_down" and share > 0.45:
            reg = "range"
        trending = reg in ("trend_up", "trend_down")
        return {
            "regime": reg, "nifty_chg_open_pct": chg, "nifty_range_pos": pos, "up": up, "down": down,
            "with_trend": reg.replace("trend_", "") if trending else None,
            "min_rr_with": TREND_RR if trending else RANGE_RR, "vwap_atr_with": TREND_VWAP_ATR if trending else RANGE_VWAP_ATR,
            "min_rr_other": RANGE_RR, "vwap_atr_other": RANGE_VWAP_ATR,
        }
    except Exception:
        return None


def format_calibration(cal: Optional[Dict]) -> str:
    if not cal:
        return ""
    head = (f"Regime: {cal['regime']} (Nifty {cal['nifty_chg_open_pct']:+.2f}% since the open, at "
            f"{cal['nifty_range_pos']}% of its range, {cal['up']} up / {cal['down']} down since the open).")
    if cal["with_trend"]:
        d = "longs" if cal["with_trend"] == "up" else "shorts"
        body = (f"- {d.capitalize()} (with the trend): minimum reward:risk {cal['min_rr_with']}, entry up to "
                f"{cal['vwap_atr_with']} ATR(14) from VWAP. A strong day is bought on pullbacks, not skipped.\n"
                f"- Trades against the trend: minimum reward:risk {cal['min_rr_other']}, entry within "
                f"{cal['vwap_atr_other']} ATR(14) of VWAP.")
    else:
        body = (f"- All trades: minimum reward:risk {cal['min_rr_other']}, entry within "
                f"{cal['vwap_atr_other']} ATR(14) of VWAP (no confirmed trend).")
    return ("\n\n## Today's calibration (computed by code from live index data)\n" + head + "\n" + body + "\n"
            "These numbers replace any minimum reward:risk or maximum VWAP distance written in your learned rules: "
            "learned rules describe patterns, the market regime sets these thresholds. Everything else in your rules "
            "(invalidation levels, stops, sizing, time rules) still applies.")
