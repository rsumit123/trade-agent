"""Step 3: a strong model proposes setup variants from backtest evidence.

The proposer only ever sees train and validation results. The out-of-sample
window is checked once at the end of a campaign, by code, so the model can't
fit to it. Every variant evaluated is appended to a ledger, so a winner is
always reported with how many variants were tried to find it.
"""

import json
from typing import Dict, List

from ..trade_context import summarise
from .variants import CHOICE_FILTERS, COMMON_RANGES, NUMERIC_FILTER_FIELDS, PARAM_RANGES

SPLITS = ("train", "val")          # never "out"
SLICES = ("regime", "time_bucket", "liquidity")


def evidence(trades: List[Dict]) -> Dict:
    """Compact train/val stats per setup/variant: overall by direction, and per slice.
    Trades from the out-of-sample window are dropped here, whatever the caller passed."""
    trades = [t for t in trades if t.get("sample") in SPLITS]
    out: Dict = {}
    for split in SPLITS:
        part = [t for t in trades if t["sample"] == split]
        out[split] = {"overall": _rows(summarise(part))}
        for s in SLICES:
            out[split][f"by_{s}"] = _rows(summarise(part, by=s), s)
    return out


def _rows(rows: List[Dict], by: str = None) -> List[Dict]:
    keep = ("setup", "direction", "n", "net_win_rate", "net", "net_per_trade", "avg_net_win",
            "avg_net_loss", "exits")
    return [{k: r[k] for k in keep + ((by,) if by else ()) if k in r} for r in rows if r["n"] >= 5]


ENGINE_RULES = """Engine (fixed, do not try to change it):
- 5-minute bars, Nifty 200 stocks, about 2 years. Signals use completed bars; entry at the next bar's open.
- Entries 09:30-14:30 IST, everything force-closed at 15:15. One trade per variant per stock per day.
- Risk is sized to INTRADAY movement: the stop from the setup is widened to at least min_stop_a5 x the
  5-minute ATR(14) (and never under 0.35% of price), and the trade is skipped if the stop would be wider
  than max_stop_a5 x the 5-minute ATR (a typical stock: 1 x 5-minute ATR is about 0.2% of price).
  Target = target_r x risk. If one bar touches both, the stop counts. In an earlier version stops and
  targets were sized from the DAILY ATR; they were so wide that over 80% of trades just ended at the
  15:15 close, so that mistake is not available any more.
- 0.05% slippage per side plus real Indian intraday charges (about Rs {charge} per Rs {notional:,.0f} round
  trip, i.e. {charge_pct:.2f}% of notional): a setup needs a real edge, tiny scalps lose to charges.
- random_entry is a control: random bar, random direction, same stop/target mechanics. A variant only
  counts if it beats random_entry, not just zero."""

BASES = """Base setups (you choose params within the ranges):
- gap_fade: |gap| >= gap_min %. After or_bars opening bars, a bar closes back through VWAP against the gap
  and beyond the day's open. Short a failing gap-up, long a failing gap-down. Stop: the day's extreme so far.
- orb_breakout: the FIRST bar to close beyond the or_bars-bar opening range (no earlier close outside it),
  with bar volume >= vol_mult x the day's average bar volume so far, and an opening range between
  min_range_pct and max_range_pct of the open. Stop: the other side of the range.
- vwap_cross: price closed on one side of VWAP for hold_bars bars, then a bar closes on the other side by
  >= min_cross_atr x the daily ATR. Stop: stop_a5 x the 5-minute ATR beyond VWAP."""


def build_prompt(evid: Dict, history: List[Dict], n_variants: int, round_no: int,
                 live_stats: str = "", notional: float = 10_000) -> str:
    from ..trade_context import est_intraday_charges
    charge = est_intraday_charges(notional, notional)
    engine_rules = ENGINE_RULES.format(charge=round(charge), notional=notional, charge_pct=charge / notional * 100)
    ranges = {b: {k: list(v) for k, v in p.items()} for b, p in PARAM_RANGES.items()}
    filters = {k: sorted(v) for k, v in CHOICE_FILTERS.items()}
    return f"""You are designing intraday trading setups for Indian equities (NSE cash, MIS), round {round_no} of a
search. Your job: propose up to {n_variants} NEW setup variants that you expect to have a positive expectancy after
costs, using the backtest evidence below. Prefer a few well-reasoned variants over many small tweaks: every
variant tried is counted, and a winner found after many tries is worth less.

{engine_rules}

{BASES}

Param ranges: {json.dumps(ranges)}
Common: {json.dumps({k: list(v) for k, v in COMMON_RANGES.items()})}
Filters on the entry context (optional):
- lists: {json.dumps(filters)}
- numeric bounds: <field>_min / <field>_max for fields {sorted(NUMERIC_FILTER_FIELDS)}
  (gap_pct signed %, abs_gap_pct, atr_pct = ATR as % of price, vix, rs_vs_nifty = stock % since open minus
  Nifty's, from_open_pct, vs_vwap_atr = distance from VWAP in ATR, range_pos 0-100 in the day's range,
  nifty_chg_open_pct, nifty_range_pos).
regime: trend_up (Nifty >= +0.4% since open and in the top 40% of its range), trend_down (mirror), else range.

Evidence (train = first ~12 months, val = next ~6 months; net = after slippage and charges, Rs per trade at
Rs {notional:,.0f} notional; slices with fewer than 5 trades are omitted):
{json.dumps(evid, separators=(",", ":"))}

Variants already tried and their results (do not repeat them):
{json.dumps(history, separators=(",", ":")) if history else "none yet"}
{("Live agents' tagged trades (small sample, for colour only):" + chr(10) + live_stats) if live_stats else ""}
What makes a good proposal:
- A clear market reason the edge should exist (who is on the other side), not just a slice that looked good.
- Consistency: positive in train AND val, and better than random_entry in both.
- Enough trades: a filter that leaves fewer than ~30 trades per split can't be judged.

Reply with ONLY a JSON array (no prose outside it), each item:
{{"id": "lowercase_snake_id", "base": "gap_fade|orb_breakout|vwap_cross", "params": {{...}},
  "target_r": 1.5, "min_stop_atr": 0.5, "filters": {{...}}, "rationale": "one or two sentences"}}"""
