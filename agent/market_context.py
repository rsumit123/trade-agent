"""
Live index context for NSE agents, from Kite: Nifty 50, Bank Nifty, India VIX,
Nifty 50 breadth, and each watchlist stock's strength relative to Nifty.

Without this the agents only knew the market's direction from news headlines,
which quote the change vs yesterday's close. On 2026-10-14 Nifty gapped up
+1.6% and then fell all day, but Opus kept calling the market "green" and
bought longs into the fade. The intraday numbers here (vs today's open, last
30 minutes) are what tell those two situations apart.

Nothing here may raise into the trading loop: fetch() returns None on failure.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INDICES = [("NIFTY 50", "NSE:NIFTY 50"), ("BANK NIFTY", "NSE:NIFTY BANK"), ("INDIA VIX", "NSE:INDIA VIX")]
TREND_MINUTES = 30
IST = ZoneInfo("Asia/Kolkata")


def _pct(a, b) -> Optional[float]:
    return round((a - b) / b * 100, 2) if a and b else None


def fetch(md, nifty50: List[str], now: Optional[datetime] = None) -> Optional[Dict]:
    """Index levels, Nifty 50 breadth and Nifty's 30-min trend. `md` is a
    KiteMarketData. Returns None before the open or on any failure."""
    try:
        stock_syms = [md._kite_symbol(t) for t in nifty50]
        quotes = md._call_with_retry("quote(market)", lambda k: k.quote([s for _, s in INDICES] + stock_syms))

        indices = []
        for name, sym in INDICES:
            q = quotes.get(sym) or {}
            o = q.get("ohlc") or {}
            last, prev, opn = q.get("last_price"), o.get("close"), o.get("open")
            if not last:
                continue
            hi, lo = o.get("high"), o.get("low")
            indices.append({
                "name": name, "last": last,
                "chg_prev_pct": _pct(last, prev), "chg_open_pct": _pct(last, opn),
                "high": hi, "low": lo,
                "range_pos_pct": round((last - lo) / (hi - lo) * 100) if hi and lo and hi > lo else None,
                "token": q.get("instrument_token"),
            })
        nifty = next((i for i in indices if i["name"] == "NIFTY 50"), None)
        if not nifty or nifty["chg_open_pct"] is None:
            return None   # pre-open: no session data yet

        nifty["trend_pct"] = _trend(md, nifty, now)

        up_prev = down_prev = up_open = down_open = 0
        for s in stock_syms:
            q = quotes.get(s) or {}
            o = q.get("ohlc") or {}
            last = q.get("last_price")
            if not last:
                continue
            if o.get("close"):
                up_prev += last > o["close"]
                down_prev += last < o["close"]
            if o.get("open"):
                up_open += last > o["open"]
                down_open += last < o["open"]

        return {"indices": indices, "nifty_chg_open_pct": nifty["chg_open_pct"],
                "breadth": {"up_prev": up_prev, "down_prev": down_prev,
                            "up_open": up_open, "down_open": down_open}}
    except Exception as e:
        logger.warning(f"Market context unavailable this cycle: {e}")
        return None


def _trend(md, nifty: Dict, now: Optional[datetime] = None) -> Optional[float]:
    """Nifty's % move over the last TREND_MINUTES, from 5-minute candles.
    Kite reads naive datetimes as IST; the container clock is UTC."""
    if not nifty.get("token"):
        return None
    try:
        now = now or datetime.now(IST).replace(tzinfo=None)
        candles = md._call_with_retry(
            "historical(nifty)",
            lambda k: k.historical_data(nifty["token"], now - timedelta(minutes=TREND_MINUTES + 10), now, "5minute"),
        )
        if not candles:
            return None
        cutoff = candles[-1]["date"] - timedelta(minutes=TREND_MINUTES)
        base = next((c for c in candles if c["date"] >= cutoff), candles[0])
        return _pct(nifty["last"], base["open"])
    except Exception as e:
        logger.debug(f"Nifty trend unavailable: {e}")
        return None


def add_relative_strength(rows: List[Dict], ctx: Optional[Dict]) -> None:
    """Set row["rs_vs_nifty"] = the stock's % move since today's open minus Nifty's."""
    if not ctx:
        return
    base = ctx["nifty_chg_open_pct"]
    for r in rows:
        if r.get("chg_open_pct") is not None:
            r["rs_vs_nifty"] = round(r["chg_open_pct"] - base, 2)


def _signed(v, suffix="%") -> str:
    return "n/a" if v is None else f"{v:+.2f}{suffix}"


def format_context(ctx: Optional[Dict]) -> str:
    if not ctx:
        return ""
    lines = []
    for i in ctx["indices"]:
        if i["name"] == "INDIA VIX":
            lines.append(f"INDIA VIX   {i['last']:.2f} ({_signed(i['chg_prev_pct'])} vs prev close, "
                         f"{_signed(i['chg_open_pct'])} since open) — rising VIX = bigger swings, wider stops")
            continue
        rng = f"{i['low']:,.2f}–{i['high']:,.2f}" if i["high"] and i["low"] else "n/a"
        pos = f", now at {i['range_pos_pct']}% of it" if i["range_pos_pct"] is not None else ""
        trend = f" · last {TREND_MINUTES} min {_signed(i['trend_pct'])}" if "trend_pct" in i else ""
        lines.append(f"{i['name']:<11} {i['last']:,.2f} · vs prev close {_signed(i['chg_prev_pct'])} · "
                     f"since today's open {_signed(i['chg_open_pct'])}{trend} · day range {rng}{pos}")
    b = ctx["breadth"]
    lines.append(f"Breadth (Nifty 50 stocks): {b['up_prev']} up / {b['down_prev']} down vs prev close · "
                 f"{b['up_open']} up / {b['down_open']} down since today's open")
    return (
        "## Market Now (live index data — use this, not news headlines, for market direction)\n"
        + "\n".join(lines) + "\n"
        "How to read it: the gap (vs prev close) is yesterday's news. Intraday direction is "
        "'since today's open' and the last 30 min. A gap-up that is falling since the open is "
        "NOT a green market for new longs. In the watchlist, vsNifty = the stock's move since "
        "the open minus Nifty's: positive = stronger than the market, negative = weaker."
    )
