"""Bar-by-bar setup simulation.

No lookahead: a setup sees completed bars 0..i; entry is at bar i+1's open.
Daily features (ATR14, previous close/turnover) come from prior days only.
If one bar touches both the stop and the target, the stop wins; a bar that
opens through a level fills at its open. Everything is closed at the 15:15
bar's open. Each trade carries the same context fields as live entries, so
trade_context.summarise() reports backtest and live trades the same way.
"""

import json
import math
from datetime import date, datetime, time
from typing import Dict, Iterable, List, Optional

import pandas as pd

from ..trade_context import liquidity_bucket, regime, time_bucket
from .setups import Day, Setup, passes_filters

ENTRY_FROM, ENTRY_UNTIL, FORCE_EXIT = time(9, 30), time(14, 30), time(15, 15)


def daily_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-day OHLCV plus prior-day features from 5-minute bars."""
    g = df.assign(day=df["date"].dt.date, tv=df["close"] * df["volume"]).groupby("day")
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum(), "turnover": g["tv"].sum()})
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["prev_close"] = pc
    d["atr"] = tr.rolling(14).mean().shift(1)
    d["prev_turnover_cr"] = (d["turnover"] / 1e7).shift(1)
    return d


def index_context(nifty: Optional[pd.DataFrame], vix: Optional[pd.DataFrame]) -> Dict[datetime, Dict]:
    """Nifty % since open and range position at each bar's close, plus VIX."""
    out: Dict[datetime, Dict] = {}
    if nifty is not None and len(nifty):
        for _, g in nifty.groupby(nifty["date"].dt.date):
            opn = g["open"].iloc[0]
            hi, lo = g["high"].cummax(), g["low"].cummin()
            for ts, c, h, l in zip(g["date"], g["close"], hi, lo):
                out[ts.to_pydatetime()] = {
                    "nifty_chg_open_pct": round((c - opn) / opn * 100, 2),
                    "nifty_range_pos": round((c - l) / (h - l) * 100) if h > l else 50,
                }
    if vix is not None and len(vix):
        for ts, c in zip(vix["date"], vix["close"]):
            out.setdefault(ts.to_pydatetime(), {})["vix"] = round(float(c), 2)
    return out


def _vwap(h, l, c, v) -> List[float]:
    out, pv, vv = [], 0.0, 0.0
    for hi, lo, cl, vo in zip(h, l, c, v):
        pv += (hi + lo + cl) / 3 * vo
        vv += vo
        out.append(pv / vv if vv else cl)
    return out


def _context(d: Day, i: int, entry_ts: datetime, idx: Dict) -> Dict:
    ctx = {"time": entry_ts.strftime("%H:%M"), "time_bucket": time_bucket(entry_ts),
           "gap_pct": round(d.gap_pct, 2),
           "from_open_pct": round((d.c[i] - d.day_open) / d.day_open * 100, 2),
           "vs_vwap_atr": round((d.c[i] - d.vwap[i]) / d.atr, 2),
           "atr_pct": round(d.atr / d.c[i] * 100, 2),
           "liquidity": liquidity_bucket(d.prev_turnover_cr),
           "date": entry_ts.date().isoformat()}
    hi, lo = max(d.h[: i + 1]), min(d.l[: i + 1])
    ctx["range_pos"] = round((d.c[i] - lo) / (hi - lo) * 100) if hi > lo else 50
    m = idx.get(d.times[i]) or {}
    if "nifty_chg_open_pct" in m:
        ctx.update(m)
        ctx["regime"] = regime(m["nifty_chg_open_pct"], m["nifty_range_pos"])
        ctx["rs_vs_nifty"] = round(ctx["from_open_pct"] - m["nifty_chg_open_pct"], 2)
    elif "vix" in m:
        ctx["vix"] = m["vix"]
    return {k: v for k, v in ctx.items() if v is not None}


def sample_of(day: date, oos_from: Optional[date], val_from: Optional[date] = None) -> str:
    """train | val | out. Proposer rounds rank on train and val only; the
    out-of-sample window is checked once per campaign."""
    if oos_from and day >= oos_from:
        return "out"
    if val_from and day >= val_from:
        return "val"
    return "in" if not val_from else "train"


def simulate_day(d: Day, setups: Iterable[Setup], symbol: str, idx: Dict, notional: float,
                 slippage: float, oos_from: Optional[date], val_from: Optional[date] = None) -> List[Dict]:
    trades = []
    n = len(d.c)
    for s in setups:
        for i in range(n - 1):
            nxt = d.times[i + 1].time()
            if d.times[i].time() < ENTRY_FROM or nxt > ENTRY_UNTIL:
                continue
            sig = s.signal(d, i)
            if not sig:
                continue
            direction, stop = sig
            ctx = _context(d, i, d.times[i + 1], idx)
            if getattr(s, "filters", None) and not passes_filters(s.filters, ctx, direction):
                continue
            long = direction == "long"
            entry = d.o[i + 1] * (1 + slippage if long else 1 - slippage)
            min_risk = s.min_stop_atr * d.atr
            risk = (entry - stop) if long else (stop - entry)
            if risk < min_risk:
                risk = min_risk
                stop = entry - risk if long else entry + risk
            target = entry + s.target_r * risk if long else entry - s.target_r * risk
            qty = math.floor(notional / entry)
            if qty < 1:
                break
            exit_px, exit_type, exit_ts = None, None, None
            for j in range(i + 1, n):
                t = d.times[j]
                if t.time() >= FORCE_EXIT:
                    exit_px, exit_type, exit_ts = d.o[j], "forced_close", t
                    break
                o, h, l = d.o[j], d.h[j], d.l[j]
                if j > i + 1 and ((long and o <= stop) or (not long and o >= stop)):
                    exit_px, exit_type, exit_ts = o, "stop_hit", t       # gapped through the stop
                    break
                hit_stop = l <= stop if long else h >= stop
                hit_tgt = h >= target if long else l <= target
                if hit_stop:                                            # stop wins a both-hit bar
                    exit_px, exit_type, exit_ts = stop, "stop_hit", t
                    break
                if hit_tgt:
                    exit_px, exit_type, exit_ts = (max(o, target) if long else min(o, target)), "target_hit", t
                    break
            if exit_px is None:                                         # data ends before 15:15
                exit_px, exit_type, exit_ts = d.c[-1], "forced_close", d.times[-1]
            exit_px *= (1 - slippage if long else 1 + slippage)
            pnl = (exit_px - entry) * qty if long else (entry - exit_px) * qty
            trades.append({
                "symbol": symbol, "setup": s.name, "base": getattr(s, "base", "") or s.name, "direction": direction,
                "quantity": qty,
                "entry_time": d.times[i + 1].isoformat(), "exit_time": exit_ts.isoformat(),
                "entry_price": round(entry, 2), "exit_price": round(exit_px, 2),
                "stop": round(stop, 2), "target": round(target, 2), "pnl": round(pnl, 2),
                "exit_type": exit_type,
                "sample": sample_of(d.times[0].date(), oos_from, val_from),
                "entry_context": json.dumps(ctx),
            })
            break                                                       # one trade per setup per day
    return trades


def run_symbol(df: pd.DataFrame, symbol: str, setups: List[Setup], idx: Dict,
               notional: float = 10_000, slippage: float = 0.0005,
               oos_from: Optional[date] = None, val_from: Optional[date] = None) -> List[Dict]:
    feats = daily_features(df)
    out = []
    for day, g in df.groupby(df["date"].dt.date):
        f = feats.loc[day]
        if pd.isna(f["atr"]) or pd.isna(f["prev_close"]) or not f["atr"] or len(g) < 10:
            continue
        h, l, c, v = (g[k].tolist() for k in ("high", "low", "close", "volume"))
        d = Day(times=[t.to_pydatetime() for t in g["date"]], o=g["open"].tolist(), h=h, l=l, c=c, v=v,
                vwap=_vwap(h, l, c, v), day_open=float(f["open"]), prev_close=float(f["prev_close"]),
                atr=float(f["atr"]), prev_turnover_cr=float(f["prev_turnover_cr"])
                if not pd.isna(f["prev_turnover_cr"]) else None)
        out.extend(simulate_day(d, setups, symbol, idx, notional, slippage, oos_from, val_from))
    return out
