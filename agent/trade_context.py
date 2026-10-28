"""
Setup tags and entry context for every new position.

The plan is to let a strong model (Opus) find intraday setups, prove which
ones have an edge, and then hand only the proven ones to cheaper models. That
needs every entry labelled with (a) the setup the agent thinks it is trading,
from a fixed list so trades can be counted, and (b) the market and stock
conditions at entry, measured by code (not described by the LLM), so results
can later be split by regime, liquidity, time of day and so on.

Nothing here may raise into the trading loop: entry_context() returns {} or a
partial dict when data is missing.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Direction comes from the action (BUY/SHORT), so setups are direction-agnostic.
SETUPS = {
    "gap_continuation": "gapped and still extending in the gap's direction",
    "gap_fade": "gap failing, moving back toward the previous close",
    "orb_breakout": "break of the opening range (first 15-30 min high/low)",
    "breakout": "break of a prior-day or multi-day high/low or key level",
    "vwap_pullback": "pullback to VWAP that holds, in the direction of the day's trend",
    "vwap_cross": "VWAP reclaimed (long) or lost (short) after trading on the other side",
    "trend_pullback": "pullback to EMA/support within a multi-day trend",
    "relative_strength": "leader (long) or laggard (short) vs the market/sector",
    "mean_reversion": "fading an overextended move (far from VWAP in ATR, RSI extreme)",
    "catalyst": "driven by specific, dated news or results",
    "other": "none of the above (explain in reason)",
}
UNTAGGED = "untagged"


def normalise_setup(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in SETUPS else (UNTAGGED if not v else "other")


def _pct(a, b) -> Optional[float]:
    return round((a - b) / b * 100, 2) if a and b else None


def time_bucket(t: datetime) -> str:
    hm = t.hour * 60 + t.minute
    if hm < 10 * 60 + 30:
        return "open"         # 09:15-10:30
    if hm < 13 * 60 + 30:
        return "midday"       # 10:30-13:30
    return "late"             # 13:30-close


def liquidity_bucket(turnover_cr: Optional[float]) -> Optional[str]:
    if turnover_cr is None:
        return None
    if turnover_cr >= 200:
        return "high"
    if turnover_cr >= 30:
        return "mid"
    return "low"


def regime(nifty_chg_open: Optional[float], nifty_range_pos: Optional[float]) -> Optional[str]:
    """Coarse intraday regime from Nifty's move since the open and where it sits in its range."""
    if nifty_chg_open is None or nifty_range_pos is None:
        return None
    if nifty_chg_open >= 0.4 and nifty_range_pos >= 60:
        return "trend_up"
    if nifty_chg_open <= -0.4 and nifty_range_pos <= 40:
        return "trend_down"
    return "range"


def build(quote: Optional[Dict], stock: Optional[Dict], market: Optional[Dict],
          now: datetime, sector: Optional[str] = None) -> Dict[str, Any]:
    """Pure function: entry context from a kite.quote() entry, get_stock_context()
    output and market_context.fetch() output. Any of them may be None."""
    from .screener import row_from_quote

    ctx: Dict[str, Any] = {"time": now.strftime("%H:%M"), "time_bucket": time_bucket(now)}

    row = row_from_quote("X", quote) if quote else None
    if row:
        ctx.update({k: row[k] for k in ("change_pct", "gap_pct", "from_open_pct", "day_range_pct",
                                        "range_pos", "vs_vwap_pct", "turnover_cr", "at_circuit")})
        ctx["liquidity"] = liquidity_bucket(row["turnover_cr"])
    stock = stock or {}
    atr, price = stock.get("atr_14"), stock.get("current_price") or (row or {}).get("ltp")
    if atr and price:
        ctx["atr_pct"] = round(atr / price * 100, 2)
        vwap = (row or {}).get("vwap")
        if vwap:
            ctx["vs_vwap_atr"] = round((price - vwap) / atr, 2)
    for k in ("vol_ratio", "rsi_14", "ema_trend", "macd_state"):
        if stock.get(k) is not None:
            ctx[k] = stock[k]
    if sector:
        ctx["sector"] = sector

    if market:
        idx = {i["name"]: i for i in market.get("indices", [])}
        n, b, v = idx.get("NIFTY 50", {}), idx.get("BANK NIFTY", {}), idx.get("INDIA VIX", {})
        ctx.update({
            "nifty_chg_prev_pct": n.get("chg_prev_pct"),
            "nifty_chg_open_pct": n.get("chg_open_pct"),
            "nifty_range_pos": n.get("range_pos_pct"),
            "nifty_trend_30m_pct": n.get("trend_pct"),
            "banknifty_chg_open_pct": b.get("chg_open_pct"),
            "vix": v.get("last"),
            "vix_chg_prev_pct": v.get("chg_prev_pct"),
        })
        br = market.get("breadth") or {}
        ctx["breadth_up_open"], ctx["breadth_down_open"] = br.get("up_open"), br.get("down_open")
        ctx["regime"] = regime(n.get("chg_open_pct"), n.get("range_pos_pct"))
        if row and row.get("from_open_pct") is not None and n.get("chg_open_pct") is not None:
            ctx["rs_vs_nifty"] = round(row["from_open_pct"] - n["chg_open_pct"], 2)
    return {k: v for k, v in ctx.items() if v is not None}


def entry_context(market_data, ticker: str, stock_ctx: Optional[Dict] = None,
                  market_ctx: Optional[Dict] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Fetch what's needed from Kite and build the context. {} outside Kite sessions."""
    if not hasattr(market_data, "_call_with_retry") or not hasattr(market_data, "_kite_symbol"):
        return {}
    try:
        sym = market_data._kite_symbol(ticker)
        quote = (market_data._call_with_retry("quote(entry)", lambda k: k.quote([sym])) or {}).get(sym)
        sector = None
        try:
            from .sector_data import get_nse_sectors
            names = [s for s, ts in get_nse_sectors().items() if ticker in ts]
            sector = ", ".join(names) or None
        except Exception:
            pass
        return build(quote, stock_ctx, market_ctx, now or datetime.now(IST), sector)
    except Exception as e:
        logger.warning(f"Entry context unavailable for {ticker}: {e}")
        return {}


def est_intraday_charges(buy_value: float, sell_value: float) -> float:
    """Approximate Indian intraday equity round-trip charges (discount broker):
    brokerage min(₹20, 0.1%) per leg, STT 0.025% on the sell leg, exchange
    0.00297% both legs, stamp 0.003% on the buy leg, 18% GST on brokerage +
    exchange fees. About ₹27 on a ₹10K round trip (Groww actual: ₹24-26)."""
    brokerage = min(20.0, 0.001 * buy_value) + min(20.0, 0.001 * sell_value)
    exchange = 0.0000297 * (buy_value + sell_value)
    stt = 0.00025 * sell_value
    stamp = 0.00003 * buy_value
    gst = 0.18 * (brokerage + exchange)
    return round(brokerage + exchange + stt + stamp + gst, 2)


def summarise(trades, by: Optional[str] = None):
    """Per-setup stats from closed trades (dicts with setup, direction, quantity,
    entry_price, exit_price, pnl, entry_context JSON). Groups by (setup,
    direction) and optionally one entry_context key. Net P&L subtracts
    est_intraday_charges so scratch trades show up as the losers they are."""
    import json
    groups: Dict[tuple, Dict[str, Any]] = {}
    for t in trades:
        if t.get("pnl") is None or t.get("exit_price") is None:
            continue
        ctx = {}
        if t.get("entry_context"):
            try:
                ctx = json.loads(t["entry_context"])
            except Exception:
                ctx = {}
        key = (t.get("setup") or UNTAGGED, (t.get("direction") or "long").lower())
        if by:
            key += (ctx.get(by, "n/a"),)
        qty, entry, exit_ = t["quantity"], t["entry_price"], t["exit_price"]
        short = key[1] == "short"
        buy_v, sell_v = (qty * exit_, qty * entry) if short else (qty * entry, qty * exit_)
        net = t["pnl"] - est_intraday_charges(buy_v, sell_v)
        g = groups.setdefault(key, {"n": 0, "wins": 0, "gross": 0.0, "net": 0.0, "win_sum": 0.0,
                                    "loss_sum": 0.0, "net_wins": 0, "exits": {}})
        g["n"] += 1
        g["gross"] += t["pnl"]
        g["net"] += net
        g["wins"] += t["pnl"] > 0
        g["net_wins"] += net > 0
        if net > 0:
            g["win_sum"] += net
        else:
            g["loss_sum"] += net
        et = t.get("exit_type") or "manual"
        g["exits"][et] = g["exits"].get(et, 0) + 1
    rows = []
    for key, g in groups.items():
        n, nw = g["n"], g["net_wins"]
        rows.append({
            "setup": key[0], "direction": key[1], **({by: key[2]} if by else {}),
            "n": n, "win_rate": round(g["wins"] / n * 100, 1),
            "net_win_rate": round(nw / n * 100, 1),
            "gross": round(g["gross"], 2), "net": round(g["net"], 2),
            "net_per_trade": round(g["net"] / n, 2),
            "avg_net_win": round(g["win_sum"] / nw, 2) if nw else None,
            "avg_net_loss": round(g["loss_sum"] / (n - nw), 2) if n - nw else None,
            "exits": g["exits"],
        })
    rows.sort(key=lambda r: (-r["n"], r["setup"]))
    return rows
