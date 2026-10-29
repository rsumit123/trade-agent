"""Candle cache for the setup backtester.

Files: <root>/<interval>/<SYMBOL>.csv.gz with columns date,open,high,low,close,volume
(date = naive IST bar start). Fetching needs Kite (VM only, after hours);
loading needs nothing but pandas.
"""

import logging
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Kite's maximum days per historical_data request, by interval.
MAX_DAYS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100,
            "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000}
COLUMNS = ["date", "open", "high", "low", "close", "volume"]
INDEX_FILES = {"NIFTY 50": "NIFTY_50", "NIFTY BANK": "NIFTY_BANK", "INDIA VIX": "INDIA_VIX"}
THROTTLE_S = 0.4   # ~3 historical calls/sec per API key, shared with live agents


def candle_path(root, interval: str, symbol: str) -> Path:
    return Path(root) / interval / f"{symbol}.csv.gz"


def in_market_hours(now: datetime) -> bool:
    """True on weekdays 09:00-15:40 IST (now is naive IST), when bulk fetches must not run."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 <= hm < 15 * 60 + 40


def chunks(start: date, end: date, max_days: int) -> List[Tuple[date, date]]:
    out, cur = [], start
    while cur <= end:
        stop = min(cur + timedelta(days=max_days - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def load(root, interval: str, symbol: str) -> Optional[pd.DataFrame]:
    p = candle_path(root, interval, symbol)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def _to_frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows or [], columns=COLUMNS)
    if len(df):
        d = pd.to_datetime(df["date"])
        if getattr(d.dt, "tz", None) is not None:
            d = d.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df["date"] = d
    return df


def fetch_symbol(historical: Callable, token: int, symbol: str, start: date, end: date,
                 root, interval: str = "5minute", throttle: float = THROTTLE_S) -> int:
    """Fetch [start, end] for one instrument, skipping what's already cached.
    historical(token, from_dt, to_dt, interval) -> list of candle dicts.
    Returns the number of new rows written."""
    existing = load(root, interval, symbol)
    ranges = [(start, end)]
    if existing is not None and len(existing):
        first, last = existing["date"].min().date(), existing["date"].max().date()
        ranges = []
        if start < first:
            ranges.append((start, first - timedelta(days=1)))
        if end > last:
            ranges.append((last + timedelta(days=1), end))
    frames = [] if existing is None else [existing]
    new = 0
    for a, b in ranges:
        for c0, c1 in chunks(a, b, MAX_DAYS[interval]):
            rows = historical(token, datetime.combine(c0, datetime.min.time()),
                              datetime.combine(c1, datetime.max.time().replace(microsecond=0)), interval)
            df = _to_frame(rows)
            new += len(df)
            frames.append(df)
            _time.sleep(throttle)
    if not new:
        return 0
    out = pd.concat([f for f in frames if len(f)]).drop_duplicates("date").sort_values("date")
    p = candle_path(root, interval, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.gz")
    out.to_csv(tmp, index=False, compression="gzip")
    tmp.replace(p)
    return new
