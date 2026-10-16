"""
Live NSE stock screener the agent can call as a tool (screen_stocks).

The pre-market scan picks ~25 stocks once at 09:15, ranked by gap size, so the
agent only ever sees the day's most extreme movers. This tool lets it screen
the whole cached NSE equity universe (~2,500 stocks) at any time with its own
filters, then shortlist names for get_stock_details.

Data: kite.quote() in batches of 500 (gives volume and the day's VWAP, which
kite.ohlc() lacks). The snapshot is cached briefly in a file shared by all
agent processes, so several screens in one cycle (or two agents) hit Kite once.
"""

import json
import logging
import os
import re
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_CACHE = Path("sessions/_screener_snapshot.json")
SNAPSHOT_TTL_S = 90
QUOTE_BATCH_SIZE = 500
DEFAULT_LIMIT = 20
MAX_LIMIT = 30
DEFAULT_MIN_TURNOVER_CR = 5.0
DEFAULT_PRICE_MIN = 50.0
DEFAULT_PRICE_MAX = 10000.0

SORT_KEYS = {
    "turnover": ("turnover_cr", True),
    "change_desc": ("change_pct", True),
    "change_asc": ("change_pct", False),
    "from_open_desc": ("from_open_pct", True),
    "from_open_asc": ("from_open_pct", False),
    "range_pos_desc": ("range_pos", True),
    "range_pos_asc": ("range_pos", False),
    "vs_vwap_desc": ("vs_vwap_pct", True),
    "vs_vwap_asc": ("vs_vwap_pct", False),
}


def _pct(a: float, b: float) -> Optional[float]:
    return round((a - b) / b * 100, 2) if b else None


def row_from_quote(sym: str, q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One compact row from a kite.quote() entry, or None if it has no usable price."""
    ltp = q.get("last_price") or 0
    ohlc = q.get("ohlc") or {}
    prev = ohlc.get("close") or 0
    if not ltp or not prev:
        return None
    opn = ohlc.get("open") or prev
    hi, lo = ohlc.get("high") or ltp, ohlc.get("low") or ltp
    vwap = q.get("average_price") or 0
    vol = q.get("volume") or 0
    upper, lower = q.get("upper_circuit_limit") or 0, q.get("lower_circuit_limit") or 0
    return {
        "ticker": f"{sym}.NS",
        "ltp": round(ltp, 2),
        "change_pct": _pct(ltp, prev),
        "gap_pct": _pct(opn, prev),
        "from_open_pct": _pct(ltp, opn),
        "day_range_pct": round((hi - lo) / prev * 100, 2) if hi > lo else 0.0,
        # 0 = at the day's low, 100 = at the day's high
        "range_pos": round((ltp - lo) / (hi - lo) * 100) if hi > lo else 50,
        "vwap": round(vwap, 2) if vwap else None,
        "vs_vwap_pct": _pct(ltp, vwap) if vwap else None,
        "turnover_cr": round(vol * (vwap or ltp) / 1e7, 2),
        "at_circuit": ("upper" if upper and ltp >= upper else
                       "lower" if lower and ltp <= lower else None),
    }


def filter_and_rank(rows: List[Dict[str, Any]], sectors: Optional[Dict[str, List[str]]] = None,
                    **f) -> List[Dict[str, Any]]:
    """Apply the tool's filters to snapshot rows and sort. Unknown keys are ignored;
    a filter left as None does not apply. sectors = {ticker: [sector names]}."""
    def num(key, default=None):
        v = f.get(key)
        return default if v is None else float(v)

    price_min, price_max = num("price_min", DEFAULT_PRICE_MIN), num("price_max", DEFAULT_PRICE_MAX)
    turnover_min = num("min_turnover_cr", DEFAULT_MIN_TURNOVER_CR)
    bounds = [(k, num(f"{k}_min"), num(f"{k}_max"))
              for k in ("change_pct", "gap_pct", "from_open_pct", "range_pos")]
    vs_vwap = (f.get("vs_vwap") or "any").lower()
    max_vwap_dist = num("max_vwap_dist_pct")
    sector = (f.get("sector") or "").strip()
    # Word-start match: "IT" must not match "Utilities", "bank" matches "PSU Bank"
    sector_re = re.compile(r"\b" + re.escape(sector), re.I) if sector else None
    sectors = sectors or {}

    out = []
    for r in rows:
        if not (price_min <= r["ltp"] <= price_max) or r["turnover_cr"] < turnover_min:
            continue
        if any((lo is not None and (r[k] is None or r[k] < lo)) or
               (hi is not None and (r[k] is None or r[k] > hi)) for k, lo, hi in bounds):
            continue
        d = r.get("vs_vwap_pct")
        if vs_vwap in ("above", "below") and (d is None or (d <= 0 if vs_vwap == "above" else d >= 0)):
            continue
        if max_vwap_dist is not None and (d is None or abs(d) > max_vwap_dist):
            continue
        names = sectors.get(r["ticker"], [])
        if sector_re and not any(sector_re.search(s) for s in names):
            continue
        out.append({**r, "sector": ", ".join(names) or None})

    key, desc = SORT_KEYS.get(f.get("sort_by") or "turnover", SORT_KEYS["turnover"])
    out.sort(key=lambda r: (r[key] is not None, r[key] if r[key] is not None else 0), reverse=desc)
    if not desc:  # keep rows with a missing key last when sorting ascending
        out.sort(key=lambda r: r[key] is None)
    return out


class StockScreener:
    """Snapshot of the whole universe + filtering. Never raises into the LLM loop."""

    def __init__(self, market_data, universe_filter: Optional[Callable[[List[str]], List[str]]] = None):
        self.market_data = market_data          # KiteMarketData: .kite + ._call_with_retry (re-auth)
        self.universe_filter = universe_filter  # e.g. runner._apply_live_universe_filter
        self._sector_map: Optional[Dict[str, List[str]]] = None
        self._syms: List[str] = []

    def _symbols(self) -> List[str]:
        if not self._syms:
            from .premarket_scanner import PreMarketScanner
            inst = PreMarketScanner(self.market_data.kite)._ensure_instrument_cache()
            self._syms = [i["tradingsymbol"] for i in inst]
        return self._syms

    def _sectors(self) -> Dict[str, List[str]]:
        if self._sector_map is None:
            from .sector_data import get_nse_sectors
            m: Dict[str, List[str]] = {}
            try:
                for name, tickers in get_nse_sectors().items():
                    for t in tickers:
                        m.setdefault(t, []).append(name)
            except Exception as e:
                logger.warning(f"Screener: sector lists unavailable: {e}")
            self._sector_map = m
        return self._sector_map

    def _quote_batch(self, batch: List[str], budget: List[int]) -> Dict[str, Any]:
        """kite.quote() for one batch; on failure retry once, then bisect (Kite's Cloudflare
        front blocks some symbol combinations). Returns what it could fetch."""
        md = self.market_data
        for attempt in range(2):
            try:
                return md._call_with_retry("quote(screener)", lambda k, b=batch: k.quote(b)) or {}
            except Exception as e:
                if len(batch) == 1 or budget[0] <= 0:
                    budget[0] -= 1
                    return {}
                if attempt == 0 and len(batch) == QUOTE_BATCH_SIZE:
                    logger.warning(f"Screener batch ({len(batch)} symbols) failed: {str(e)[:120]}")
                    _time.sleep(1)
                    continue
                break
        mid = len(batch) // 2
        out = self._quote_batch(batch[:mid], budget)
        out.update(self._quote_batch(batch[mid:], budget))
        return out

    def _fetch_snapshot(self) -> Dict[str, Any]:
        symbols = self._symbols()
        started = _time.monotonic()
        rows, budget = [], [25]
        kite_syms = [f"NSE:{s}" for s in symbols]
        for i in range(0, len(kite_syms), QUOTE_BATCH_SIZE):
            if i:
                _time.sleep(1)  # Kite quote rate limit: 1 request/second
            got = self._quote_batch(kite_syms[i:i + QUOTE_BATCH_SIZE], budget)
            for k, q in got.items():
                row = row_from_quote(k.split(":", 1)[-1], q)
                if row:
                    rows.append(row)
        missing = len(symbols) - len(rows)
        logger.info(f"🔎 Screener snapshot: {len(rows)}/{len(symbols)} stocks in "
                    f"{_time.monotonic() - started:.1f}s")
        return {"ts": _time.time(), "as_of": datetime.now(IST).strftime("%H:%M:%S"),
                "universe": len(symbols), "missing": missing, "rows": rows}

    def snapshot(self) -> Dict[str, Any]:
        try:
            snap = json.loads(SNAPSHOT_CACHE.read_text())
            if _time.time() - snap.get("ts", 0) < SNAPSHOT_TTL_S and snap.get("rows"):
                return snap
        except Exception:
            pass
        snap = self._fetch_snapshot()
        if snap["rows"]:
            try:
                SNAPSHOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
                tmp = SNAPSHOT_CACHE.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(snap))
                tmp.replace(SNAPSHOT_CACHE)
            except Exception as e:
                logger.warning(f"Screener: could not cache snapshot: {e}")
        return snap

    def screen(self, **filters) -> Dict[str, Any]:
        try:
            snap = self.snapshot()
        except Exception as e:
            logger.error(f"Screener failed: {e}")
            return {"error": f"Screener unavailable right now: {str(e)[:150]}"}
        if not snap["rows"]:
            return {"error": "Screener got no market data from Kite; try again next cycle"}
        try:
            limit = max(1, min(int(filters.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        matched = filter_and_rank(snap["rows"], self._sectors(), **filters)
        if self.universe_filter and matched:
            # Keep enough candidates to fill the limit after dropping non-tradable names
            allowed = set(self.universe_filter([r["ticker"] for r in matched[:limit * 4]]))
            matched = [r for r in matched[:limit * 4] if r["ticker"] in allowed]
        result = {
            "as_of_ist": snap["as_of"],
            "universe_scanned": snap["universe"] - snap["missing"],
            "matched": len(matched),
            "results": matched[:limit],
            "fields": ("change_pct vs previous close; gap_pct open vs previous close; from_open_pct "
                       "since today's open; range_pos 0=day low..100=day high; vs_vwap_pct vs "
                       "today's VWAP; turnover_cr = traded value today in ₹ crore. Use "
                       "get_stock_details for indicators before trading."),
        }
        if snap["missing"] > snap["universe"] * 0.05:
            result["incomplete"] = f"{snap['missing']} of {snap['universe']} stocks had no data this time"
        return result
