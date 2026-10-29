"""Exact setup rules for the backtester.

A setup looks at a stock-day up to and including completed bar i and returns
(direction, stop) or None. The engine enters at bar i+1's open, widens the
stop to at least min_stop_atr x ATR, and sets the target at target_r x risk.
Params are plain data so step 3 (Opus writing setup definitions) can produce
variants without new code.
"""

import random
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

Signal = Optional[Tuple[str, float]]   # ("long"|"short", stop price)


@dataclass
class Day:
    """One stock-day. Arrays are per 5-minute bar; scalars come from prior days."""
    times: list           # datetime of each bar's start
    o: list
    h: list
    l: list
    c: list
    v: list
    vwap: list            # cumulative from the day's first bar
    day_open: float
    prev_close: float
    atr: float            # ATR(14) of prior days
    prev_turnover_cr: Optional[float] = None

    @property
    def gap_pct(self) -> float:
        return (self.day_open - self.prev_close) / self.prev_close * 100


@dataclass
class Setup:
    name: str
    params: Dict = field(default_factory=dict)
    target_r: float = 1.5
    min_stop_atr: float = 0.5
    filters: Dict = field(default_factory=dict)   # entry-context filters, see passes_filters()
    base: str = ""                                # registry name when this is a variant

    def signal(self, d: Day, i: int) -> Signal:
        raise NotImplementedError


def passes_filters(filters: Dict, ctx: Dict, direction: str) -> bool:
    """Entry-context filters. Keys: direction, regime, time_bucket, liquidity
    (allowed lists) and <field>_min / <field>_max for any numeric context field
    (gap_pct, abs_gap_pct, atr_pct, vix, rs_vs_nifty, from_open_pct, ...).
    A filter on a field the trade doesn't have rejects the trade."""
    for key, want in (filters or {}).items():
        if key == "direction":
            if direction not in want:
                return False
        elif key in ("regime", "time_bucket", "liquidity"):
            if ctx.get(key) not in want:
                return False
        elif key.endswith("_min") or key.endswith("_max"):
            field_, bound = key[:-4], key[-3:]
            v = abs(ctx.get("gap_pct")) if field_ == "abs_gap_pct" and "gap_pct" in ctx else ctx.get(field_)
            if v is None or (bound == "min" and v < want) or (bound == "max" and v > want):
                return False
    return True


class GapFade(Setup):
    """|gap| >= gap_min %. After the opening range, a bar closes back through VWAP
    against the gap and beyond the day's open. Stop: the day's extreme so far."""

    def __init__(self, gap_min=2.0, or_bars=3, **kw):
        super().__init__("gap_fade", {"gap_min": gap_min, "or_bars": or_bars}, **kw)

    def signal(self, d: Day, i: int) -> Signal:
        p = self.params
        if i < p["or_bars"]:
            return None
        g = d.gap_pct
        if g >= p["gap_min"] and d.c[i] < d.vwap[i] and d.c[i] < d.day_open:
            return "short", max(d.h[: i + 1])
        if g <= -p["gap_min"] and d.c[i] > d.vwap[i] and d.c[i] > d.day_open:
            return "long", min(d.l[: i + 1])
        return None


class OrbBreakout(Setup):
    """A bar closes beyond the opening range (or_bars bars) on volume >= vol_mult x
    the day's average bar volume so far. Stop: the other side of the range."""

    def __init__(self, or_bars=3, vol_mult=1.5, **kw):
        super().__init__("orb_breakout", {"or_bars": or_bars, "vol_mult": vol_mult}, **kw)

    def signal(self, d: Day, i: int) -> Signal:
        p = self.params
        n = p["or_bars"]
        if i < n:
            return None
        orh, orl = max(d.h[:n]), min(d.l[:n])
        avg = sum(d.v[:i]) / i if i else 0
        if not avg or d.v[i] < p["vol_mult"] * avg:
            return None
        if d.c[i] > orh and d.c[i - 1] <= orh:
            return "long", orl
        if d.c[i] < orl and d.c[i - 1] >= orl:
            return "short", orh
        return None


class VwapCross(Setup):
    """Price held one side of VWAP for hold_bars bars, then a bar closes on the
    other side by >= min_cross_atr x ATR. Stop: stop_atr x ATR beyond VWAP."""

    def __init__(self, hold_bars=6, min_cross_atr=0.1, stop_atr=0.5, **kw):
        super().__init__("vwap_cross", {"hold_bars": hold_bars, "min_cross_atr": min_cross_atr,
                                        "stop_atr": stop_atr}, **kw)

    def signal(self, d: Day, i: int) -> Signal:
        p = self.params
        k = p["hold_bars"]
        if i < k or not d.atr:
            return None
        prior = range(i - k, i)
        above = all(d.c[j] > d.vwap[j] for j in prior)
        below = all(d.c[j] < d.vwap[j] for j in prior)
        x = p["min_cross_atr"] * d.atr
        if above and d.c[i] < d.vwap[i] - x:
            return "short", d.vwap[i] + p["stop_atr"] * d.atr
        if below and d.c[i] > d.vwap[i] + x:
            return "long", d.vwap[i] - p["stop_atr"] * d.atr
        return None


class RandomEntry(Setup):
    """Control: enters in a random direction on a random eligible bar, with the
    same stop/target mechanics (stop min_stop_atr x ATR away, target_r x risk).
    A real setup has to beat this, not just zero. Deterministic per stock-day."""

    def __init__(self, prob=0.05, seed=7, **kw):
        super().__init__("random_entry", {"prob": prob, "seed": seed}, **kw)

    def signal(self, d: Day, i: int) -> Signal:
        r = random.Random(f"{self.params['seed']}|{d.times[i].isoformat()}|{d.day_open}|{d.prev_close}")
        if r.random() >= self.params["prob"]:
            return None
        return ("long", d.c[i]) if r.random() < 0.5 else ("short", d.c[i])   # stop widened to min_stop_atr


REGISTRY = {"gap_fade": GapFade, "orb_breakout": OrbBreakout, "vwap_cross": VwapCross,
            "random_entry": RandomEntry}

