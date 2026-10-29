"""Setup variants as data: what the proposer (Opus) may ask for, validated
against explicit ranges so the search space stays bounded and nonsense is
rejected before it costs a backtest.

A variant spec:
    {"id": "gap_fade_g3_down", "base": "gap_fade",
     "params": {"gap_min": 3.0}, "target_r": 2.0, "min_stop_atr": 0.5,
     "filters": {"regime": ["trend_down", "range"], "direction": ["short"]},
     "rationale": "..."}
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .setups import REGISTRY, Setup

# (min, max) per numeric param, by base setup.
PARAM_RANGES = {
    "gap_fade": {"gap_min": (1.0, 6.0), "or_bars": (2, 6)},
    "orb_breakout": {"or_bars": (2, 6), "vol_mult": (1.0, 4.0)},
    "vwap_cross": {"hold_bars": (3, 18), "min_cross_atr": (0.0, 0.5), "stop_atr": (0.2, 1.5)},
}
COMMON_RANGES = {"target_r": (1.0, 3.0), "min_stop_atr": (0.3, 1.5)}
CHOICE_FILTERS = {
    "direction": {"long", "short"},
    "regime": {"trend_up", "trend_down", "range"},
    "time_bucket": {"open", "midday", "late"},
    "liquidity": {"high", "mid", "low"},
}
NUMERIC_FILTER_FIELDS = {"gap_pct", "abs_gap_pct", "atr_pct", "vix", "rs_vs_nifty", "from_open_pct",
                         "vs_vwap_atr", "range_pos", "nifty_chg_open_pct", "nifty_range_pos"}
ID_RE = re.compile(r"^[a-z0-9_]{3,48}$")


def validate(spec: Dict) -> Tuple[Optional[Setup], List[str]]:
    """Build a Setup from a spec, or return the reasons it was rejected."""
    errs: List[str] = []
    vid, base = spec.get("id", ""), spec.get("base", "")
    if not ID_RE.match(str(vid)):
        errs.append(f"id {vid!r} must be 3-48 chars of a-z0-9_")
    if base not in PARAM_RANGES:
        return None, errs + [f"base {base!r} must be one of {sorted(PARAM_RANGES)}"]
    params = spec.get("params") or {}
    for k, v in params.items():
        rng = PARAM_RANGES[base].get(k)
        if rng is None:
            errs.append(f"{base} has no param {k!r} (allowed: {sorted(PARAM_RANGES[base])})")
        elif not isinstance(v, (int, float)) or not rng[0] <= v <= rng[1]:
            errs.append(f"{k}={v!r} outside {rng}")
    common = {}
    for k, rng in COMMON_RANGES.items():
        if k in spec:
            v = spec[k]
            if not isinstance(v, (int, float)) or not rng[0] <= v <= rng[1]:
                errs.append(f"{k}={v!r} outside {rng}")
            else:
                common[k] = float(v)
    filters = spec.get("filters") or {}
    for k, v in filters.items():
        if k in CHOICE_FILTERS:
            if not isinstance(v, list) or not v or not set(v) <= CHOICE_FILTERS[k]:
                errs.append(f"filter {k} must be a non-empty list from {sorted(CHOICE_FILTERS[k])}")
        elif (k.endswith("_min") or k.endswith("_max")) and k[:-4] in NUMERIC_FILTER_FIELDS:
            if not isinstance(v, (int, float)):
                errs.append(f"filter {k} must be a number")
        else:
            errs.append(f"unknown filter {k!r}")
    if errs:
        return None, errs
    ints = {k: int(v) for k, v in params.items() if isinstance(PARAM_RANGES[base][k][0], int)}
    setup = REGISTRY[base](**{**params, **ints}, **common)
    setup.name, setup.base, setup.filters = vid, base, dict(filters)
    return setup, []


def parse_specs(text: str) -> List[Dict]:
    """Variant specs from a model reply: a JSON array, optionally in a ```json block."""
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("["): text.rfind("]") + 1]
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of variant specs")
    return [d for d in data if isinstance(d, dict)]


def log_results(ledger: Path, campaign: str, round_no: int, spec: Dict, results: Dict) -> None:
    """Append one evaluated variant to the multiple-testing ledger."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "campaign": campaign,
                            "round": round_no, "spec": spec, "results": results}) + "\n")


def tried_count(ledger: Path, campaign: Optional[str] = None) -> int:
    if not ledger.exists():
        return 0
    n = 0
    for line in ledger.read_text().splitlines():
        try:
            if campaign is None or json.loads(line).get("campaign") == campaign:
                n += 1
        except ValueError:
            pass
    return n
