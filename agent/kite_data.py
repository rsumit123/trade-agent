"""
Kite Market Data — Real-time market data provider using Zerodha Kite Connect API.

Drop-in replacement for MarketData (yfinance). Same interface, real-time data.
Uses kite.quote() for live prices, kite.historical_data() for candles/technicals.
"""

import fcntl
import json
import logging
import math
import os
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
# Past daily candles change once a day, so all agents share one cache file per
# day instead of each pulling 100 days of history per stock per cycle.
DAILY_CACHE_DIR = Path("sessions/_daily_candles")
HISTORY_DAYS = 100        # ≈ 65+ trading days, so EMA(50) is available
HIST_THROTTLE_S = 0.4     # Kite allows ~3 historical calls/sec per API key
INSTRUMENT_LOAD_DELAYS = (2, 5)     # backoff between startup attempts to load the instrument list
INSTRUMENT_RELOAD_MIN_S = 60        # at most one lazy reload attempt a minute while the map is empty
NIFTY_TOKEN = 256265                # NIFTY 50 index — used to detect exchange holidays
HOLIDAY_PROBE_AFTER = dtime(9, 20)  # first minute candles exist by now on a real trading day
HOLIDAY_PROBE_RETRY_S = 300         # after a failed probe, don't hammer Kite from the per-minute monitor
HOLIDAY_CONFIRM_S = 600             # a holiday needs two empty probes this far apart
HOLIDAY_RECHECK_S = 1800            # and is re-checked this often, in case candles were just late


def macd_state_from(hist: pd.Series) -> Optional[str]:
    """Honest MACD label from the histogram series (MACD - signal). A *cross* is
    the histogram changing sign: 'bullish_cross'/'bearish_cross' on the latest bar,
    '..._cross_1bar_ago' one bar earlier; otherwise just which side of the signal
    line the MACD is on."""
    if hist is None or len(hist) < 3:
        return None
    h0, h1, h2 = (float(x) for x in hist.iloc[-1:-4:-1])
    if h0 > 0:
        side, kind = "above_signal", "bullish"
    elif h0 < 0:
        side, kind = "below_signal", "bearish"
    else:
        return "neutral"
    prior = lambda h: h <= 0 if h0 > 0 else h >= 0
    if prior(h1):
        return f"{kind}_cross"
    if prior(h2):
        return f"{kind}_cross_1bar_ago"
    return side


def _with_today_bar(past: pd.DataFrame, quote: dict) -> pd.DataFrame:
    """Past daily candles plus today's bar built from the live quote."""
    ohlc = quote.get("ohlc") or {}
    last = quote.get("last_price")
    if not last or not ohlc.get("open"):
        return past
    today = pd.DataFrame([{
        "open": ohlc["open"], "high": max(ohlc.get("high") or last, last),
        "low": min(ohlc.get("low") or last, last), "close": last,
        "volume": quote.get("volume", 0) or 0,
    }])
    return pd.concat([past, today], ignore_index=True)


def compute_technicals(df: pd.DataFrame, current_price: float, volume: float) -> Dict[str, Any]:
    """Daily indicators from candles whose last row is today. Used by both the
    watchlist rows and get_stock_details so the two always agree."""
    out = {"rsi_14": None, "atr_14": None, "sma_5": None, "ema_20": None, "ema_50": None,
           "macd": None, "macd_signal": None, "macd_hist": None, "macd_state": None,
           "vol_ratio": None, "avg_volume_20d": None, "high_5d": None, "low_5d": None}
    if len(df) >= 15:
        close = df["close"]

        # RSI(14) — Wilder smoothing
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, float('nan'))
        rsi = (100 - (100 / (1 + rs))).where(avg_loss > 0, 100.0)   # no losses = RSI 100
        out["rsi_14"] = round(float(rsi.iloc[-1]), 1)

        # ATR(14)
        h = df["high"]
        l = df["low"]
        c = close.shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        atr = tr.ewm(com=13, min_periods=14).mean()
        out["atr_14"] = round(float(atr.iloc[-1]), 2)

        out["sma_5"] = round(float(close.rolling(5).mean().iloc[-1]), 2)

        # EMA(20) and EMA(50) — trend filters
        if len(df) >= 20:
            out["ema_20"] = round(float(close.ewm(span=20, adjust=False).mean().iloc[-1]), 2)
        if len(df) >= 50:
            out["ema_50"] = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)

        # MACD(12, 26, 9) — trend & momentum
        if len(df) >= 26:
            ema_12 = close.ewm(span=12, adjust=False).mean()
            ema_26 = close.ewm(span=26, adjust=False).mean()
            macd = ema_12 - ema_26
            signal = macd.ewm(span=9, adjust=False).mean()
            out["macd"] = round(float(macd.iloc[-1]), 2)
            out["macd_signal"] = round(float(signal.iloc[-1]), 2)
            out["macd_hist"] = round(out["macd"] - out["macd_signal"], 2)
            out["macd_state"] = macd_state_from(macd - signal)

        # Volume ratio: today's volume so far / 20-day average (today included)
        avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
        if avg_vol > 0:
            out["vol_ratio"] = round(volume / avg_vol, 2)

    # 5-day high/low (today included) from whatever history exists; with no past
    # bar there is nothing to measure against, so res/sup stay None (n/a), not 0.0%.
    if len(df) >= 2:
        out["high_5d"] = float(df["high"].tail(5).max())
        out["low_5d"] = float(df["low"].tail(5).min())
    # Real average daily volume from past bars only (today's partial volume excluded)
    if len(df) >= 2:
        past_vol = df["volume"].iloc[:-1].tail(20)
        if past_vol.mean() > 0:
            out["avg_volume_20d"] = int(past_vol.mean())

    ok = bool(current_price) and out["high_5d"] is not None
    out["dist_to_resistance_pct"] = round((out["high_5d"] - current_price) / current_price * 100, 2) if ok else None
    out["dist_to_support_pct"] = round((current_price - out["low_5d"]) / current_price * 100, 2) if ok else None
    return out


def _vs_sma(price: float, sma_5: Optional[float]) -> str:
    return "unknown" if not sma_5 else "above" if price > sma_5 else "below"


class KiteMarketData:
    """Market data provider using Zerodha Kite Connect API."""

    def __init__(self, watchlist: List[str], market_preset=None, kite_client=None, auth=None):
        self.watchlist = watchlist
        self.market_preset = market_preset
        self.kite = kite_client
        self._auth = auth  # KiteAuth handle so we can re-issue a token if Kite revokes
        self._instrument_map: Dict[str, int] = {}  # tradingsymbol → instrument_token
        self._tick_sizes: Dict[str, float] = {}    # tradingsymbol → tick size (₹)
        self._price_cache: Dict[str, float] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=30)  # 30s cache (much fresher than yfinance 5min)
        self._instruments_tried_at = 0.0
        self._holiday: Optional[tuple] = None        # (IST date, is_holiday) once probed
        self._holiday_empty: Optional[tuple] = None  # (IST date, monotonic) of the last empty probe
        self._holiday_probe_failed_at = -HOLIDAY_PROBE_RETRY_S

        if self.kite:
            for delay in (*INSTRUMENT_LOAD_DELAYS, None):
                if self._load_instruments():
                    break
                if delay is not None:
                    time.sleep(delay)

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return ("incorrect" in msg and ("api_key" in msg or "access_token" in msg)) \
            or "tokenexception" in msg or "token expired" in msg

    def _refresh_kite(self) -> bool:
        """Force a re-auth and swap in the new client. Returns True on success."""
        if not self._auth:
            return False
        try:
            self.kite = self._auth.refresh_client(getattr(self.kite, "access_token", None))
            logger.info("🔑 Kite client re-authenticated after token rejection")
            return True
        except Exception as e:
            logger.error(f"Kite re-auth failed: {e}")
            return False

    def _call_with_retry(self, label: str, fn):
        """Run a Kite API call; if it fails with an auth error, re-auth and retry once."""
        try:
            return fn(self.kite)
        except Exception as e:
            if self._is_auth_error(e) and self._refresh_kite():
                try:
                    return fn(self.kite)
                except Exception as e2:
                    logger.error(f"Kite {label} failed after re-auth: {e2}")
                    raise
            raise

    def _load_instruments(self) -> bool:
        """Cache instrument tokens for quick lookup. Returns True on success."""
        self._instruments_tried_at = time.monotonic()
        try:
            instruments = self.kite.instruments("NSE")
            for i in instruments:
                sym = i["tradingsymbol"]
                self._instrument_map[sym] = i["instrument_token"]
                # Also map with .NS suffix for compatibility
                self._instrument_map[f"{sym}.NS"] = i["instrument_token"]
                if i.get("tick_size"):
                    self._tick_sizes[sym] = float(i["tick_size"])
            logger.info(f"📊 Loaded {len(instruments)} NSE instruments from Kite")
            return bool(instruments)
        except Exception as e:
            logger.error(f"Failed to load Kite instruments: {e}")
            return False

    def _strip_ns(self, ticker: str) -> str:
        """Remove .NS suffix: 'RELIANCE.NS' → 'RELIANCE'"""
        return ticker.replace(".NS", "")

    def _kite_symbol(self, ticker: str) -> str:
        """Convert to Kite format: 'RELIANCE.NS' → 'NSE:RELIANCE'"""
        return f"NSE:{self._strip_ns(ticker)}"

    def tick_size(self, ticker: str) -> Optional[float]:
        """NSE price tick for a ticker (e.g. 0.05), or None if unknown."""
        return self._tick_sizes.get(self._strip_ns(ticker))

    def _get_instrument_token(self, ticker: str) -> Optional[int]:
        """Get instrument token for a ticker."""
        if (not self._instrument_map and self.kite
                and time.monotonic() - self._instruments_tried_at >= INSTRUMENT_RELOAD_MIN_S):
            self._load_instruments()   # startup load failed — don't stay blind all day
        return self._instrument_map.get(ticker) or self._instrument_map.get(self._strip_ns(ticker))

    # ── Core Data Methods (same interface as MarketData) ─────

    def past_daily(self, tickers: List[str]) -> Dict[str, pd.DataFrame]:
        """Daily candles before today for each ticker, from a per-day cache file
        shared by all agents. Tickers whose history can't be fetched are left
        out (their indicators show n/a) and retried next cycle."""
        today = datetime.now(IST).date()
        mem = getattr(self, "_daily_mem", None)
        if mem is None or mem.get("date") != today:
            mem = self._daily_mem = {"date": today, "data": {}}
        want = [t for t in dict.fromkeys(tickers) if t not in mem["data"]]
        if want:
            DAILY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = DAILY_CACHE_DIR / f"{today.isoformat()}.json"
            with open(path.with_suffix(".lock"), "w") as lockf:
                fcntl.flock(lockf, fcntl.LOCK_EX)      # one agent fetches at a time
                try:
                    cache = json.loads(path.read_text()) if path.exists() else {}
                except ValueError:
                    cache = {}
                fetched = False
                for t in want:
                    if t in cache:
                        continue
                    token = self._get_instrument_token(t)
                    if not token:
                        continue
                    try:
                        candles = self._call_with_retry(
                            "historical_data(daily)",
                            lambda k, tok=token: k.historical_data(
                                instrument_token=tok,
                                from_date=(today - timedelta(days=HISTORY_DAYS)).isoformat(),
                                to_date=(today - timedelta(days=1)).isoformat(),
                                interval="day",
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Daily history unavailable for {t}: {e}")
                        continue
                    finally:
                        time.sleep(HIST_THROTTLE_S)
                    cache[t] = [[str(c["date"])[:10], c["open"], c["high"], c["low"], c["close"], c["volume"]]
                                for c in candles if str(c["date"])[:10] < today.isoformat()]
                    fetched = True
                if fetched:
                    tmp = path.with_suffix(f".{os.getpid()}.tmp")
                    tmp.write_text(json.dumps(cache))
                    os.replace(tmp, path)
                fcntl.flock(lockf, fcntl.LOCK_UN)
            for t in want:
                if t in cache:
                    mem["data"][t] = pd.DataFrame(
                        cache[t], columns=["date", "open", "high", "low", "close", "volume"])
        return {t: mem["data"][t] for t in tickers if t in mem["data"]}

    def get_current_prices(self, tickers: List[str] = None) -> Dict[str, float]:
        """Get real-time LTP for watchlist or specific tickers."""
        symbols = tickers or self.watchlist

        # Check cache
        if (self._cache_time and
            datetime.now() - self._cache_time < self._cache_ttl and
            not tickers):  # Don't use cache for specific ticker requests
            return self._price_cache

        try:
            # Kite quote API accepts up to 500 instruments per call
            kite_symbols = [self._kite_symbol(t) for t in symbols]

            # Batch in chunks of 500
            prices = {}
            for i in range(0, len(kite_symbols), 500):
                batch = kite_symbols[i:i+500]
                quotes = self._call_with_retry("quote(prices)", lambda k, b=batch: k.quote(b))
                for sym, data in quotes.items():
                    # Convert back: "NSE:RELIANCE" → "RELIANCE.NS"
                    ticker_ns = f"{sym.split(':')[1]}.NS"
                    prices[ticker_ns] = data["last_price"]

            if not tickers:
                self._price_cache = prices
                self._cache_time = datetime.now()

            return prices
        except Exception as e:
            logger.error(f"Kite get_current_prices failed: {e}")
            # Callers asking for specific tickers (the stop monitor, order pricing)
            # must not get the cached watchlist prices, which can be minutes old.
            return {} if tickers else (self._price_cache or {})

    def get_stock_context(self, ticker: str, include_vwap: bool = True) -> Dict[str, Any]:
        """Get rich technical context for a single stock using Kite data."""
        try:
            kite_sym = self._kite_symbol(ticker)
            token = self._get_instrument_token(ticker)

            # Get live quote
            quote = self._call_with_retry(
                "quote(stock_context)",
                lambda k, s=kite_sym: k.quote([s]),
            ).get(kite_sym, {})
            if not quote.get("last_price"):
                return {"ticker": ticker, "error": f"No quote available for {ticker}"}
            current_price = quote["last_price"]
            ohlc = quote.get("ohlc", {})
            prev_close = ohlc.get("close", current_price)
            day_open = ohlc.get("open", current_price)
            day_high = ohlc.get("high", current_price)
            day_low = ohlc.get("low", current_price)
            volume = quote.get("volume", 0)

            change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

            # Daily history (cached once a day, shared by all agents) plus
            # today's bar from the live quote — the same series the watchlist uses.
            try:
                past = self.past_daily([ticker]).get(ticker)
            except Exception as e:
                logger.warning(f"Daily history cache unavailable: {e}")
                past = None
            df = _with_today_bar(past, quote) if past is not None else pd.DataFrame()
            t = compute_technicals(df, current_price, volume)
            rsi_14, atr_14, sma_5 = t["rsi_14"], t["atr_14"], t["sma_5"]
            ema_20, ema_50 = t["ema_20"], t["ema_50"]
            macd_line, macd_signal, macd_hist = t["macd"], t["macd_signal"], t["macd_hist"]
            vol_ratio, high_5d, low_5d = t["vol_ratio"], t["high_5d"], t["low_5d"]
            dist_to_resistance, dist_to_support = t["dist_to_resistance_pct"], t["dist_to_support_pct"]

            # VWAP from Kite quote (built-in, no extra call needed!)
            vwap = quote.get("average_price", None)  # Kite provides VWAP as average_price

            # Trend assessment from EMA20/50
            ema_trend = None
            if ema_20 and ema_50:
                ema_trend = "bullish" if ema_20 > ema_50 else "bearish"
            price_vs_ema20 = "above" if (ema_20 and current_price > ema_20) else "below" if ema_20 else None

            macd_state = t["macd_state"]

            result = {
                "ticker": ticker,
                "current_price": current_price,
                "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
                "high_5d": high_5d,
                "low_5d": low_5d,
                "dist_to_resistance_pct": dist_to_resistance,
                "dist_to_support_pct": dist_to_support,
                "sma_5": sma_5,
                "price_vs_sma": _vs_sma(current_price, sma_5),
                "ema_20": ema_20,
                "ema_50": ema_50,
                "ema_trend": ema_trend,
                "price_vs_ema20": price_vs_ema20,
                "macd": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
                "macd_state": macd_state,
                "today_volume": int(volume),
                "avg_volume_20d": t["avg_volume_20d"],
                "rsi_14": rsi_14,
                "atr_14": atr_14,
                "vol_ratio": vol_ratio,
            }

            if include_vwap and vwap:
                result["vwap"] = round(vwap, 2)
                result["price_vs_vwap"] = "above" if current_price > vwap else "below"

            return result

        except Exception as e:
            logger.error(f"Kite get_stock_context failed for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        """Batch fetch all watchlist stocks with technicals."""
        try:
            # Batch OHLC for all watchlist — much faster than individual calls
            kite_symbols = [self._kite_symbol(t) for t in self.watchlist]

            # Fetch quotes in batches of 500
            all_quotes = {}
            for i in range(0, len(kite_symbols), 500):
                batch = kite_symbols[i:i+500]
                quotes = self._call_with_retry("quote(watchlist)", lambda k, b=batch: k.quote(b))
                all_quotes.update(quotes)

            try:
                history = self.past_daily(self.watchlist)
            except Exception as e:   # indicators show n/a; prices still flow
                logger.warning(f"Daily history cache unavailable: {e}")
                history = {}
            summaries = []
            dropped = []
            for ticker in self.watchlist:
                kite_sym = self._kite_symbol(ticker)
                quote = all_quotes.get(kite_sym, {})

                if not quote or not quote.get("last_price"):
                    dropped.append(ticker)
                    continue

                current_price = quote["last_price"]
                ohlc = quote.get("ohlc", {})
                prev_close = ohlc.get("close", current_price)
                change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
                day_open = ohlc.get("open")
                chg_open_pct = round((current_price - day_open) / day_open * 100, 2) if day_open else None
                volume = quote.get("volume", 0)
                vwap = quote.get("average_price")

                # Daily indicators from the shared once-a-day history plus today's
                # bar, so these match get_stock_details exactly.
                past = history.get(ticker)
                t = (compute_technicals(_with_today_bar(past, quote), current_price, volume)
                     if past is not None and day_open else {})
                day_high, day_low = ohlc.get("high"), ohlc.get("low")

                summaries.append({
                    "ticker": ticker,
                    "current_price": round(current_price, 2),
                    "change_pct": round(change_pct, 2),
                    "chg_open_pct": chg_open_pct,
                    "rsi_14": t.get("rsi_14"),
                    "atr_14": t.get("atr_14"),
                    "vol_ratio": t.get("vol_ratio"),
                    "dist_to_resistance_pct": t.get("dist_to_resistance_pct"),
                    "dist_to_support_pct": t.get("dist_to_support_pct"),
                    "sma_5": t.get("sma_5"),
                    "price_vs_sma": _vs_sma(current_price, t.get("sma_5")),
                    "vwap": round(vwap, 2) if vwap else None,
                    "vwap_dist_pct": round((current_price - vwap) / vwap * 100, 2) if vwap else None,
                    "from_day_high_pct": round((current_price - day_high) / day_high * 100, 2) if day_high and day_open else None,
                    "from_day_low_pct": round((current_price - day_low) / day_low * 100, 2) if day_low and day_open else None,
                    "volume": volume,
                })

            # Sort by absolute change (biggest movers first)
            summaries.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
            if dropped:
                logger.warning(
                    f"⚠️  Watchlist: {len(dropped)} of {len(self.watchlist)} tickers had no quote — "
                    f"dropped: {', '.join(dropped[:8])}{'…' if len(dropped) > 8 else ''}"
                )
            return summaries

        except Exception as e:
            logger.error(f"Kite get_watchlist_summary failed: {e}")
            return []

    def get_intraday_data(self, ticker: str, interval: str = "5minute") -> pd.DataFrame:
        """Get intraday candle data from Kite."""
        try:
            token = self._get_instrument_token(ticker)
            if not token:
                return pd.DataFrame()

            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = from_date

            candles = self._call_with_retry(
                "historical_data(intraday)",
                lambda k, t=token, fd=from_date, td=to_date, iv=interval: k.historical_data(
                    instrument_token=t, from_date=fd, to_date=td, interval=iv,
                ),
            )
            return pd.DataFrame(candles)
        except Exception as e:
            logger.error(f"Kite intraday data failed for {ticker}: {e}")
            return pd.DataFrame()

    def is_market_open(self) -> bool:
        """Check if the market is currently open (same logic as yfinance version)."""
        if self.market_preset and self.market_preset.is_24x7:
            return True

        from zoneinfo import ZoneInfo

        tz_str = self.market_preset.timezone if self.market_preset else "Asia/Kolkata"
        open_str = self.market_preset.market_open if self.market_preset else "09:15"
        close_str = self.market_preset.market_close if self.market_preset else "15:30"

        now = datetime.now(ZoneInfo(tz_str))
        if now.weekday() > 4:
            return False

        open_h, open_m = map(int, open_str.split(":"))
        close_h, close_m = map(int, close_str.split(":"))
        market_open = now.replace(hour=open_h, minute=open_m, second=0)
        market_close = now.replace(hour=close_h, minute=close_m, second=0)
        if not (market_open <= now <= market_close):
            return False
        if self.kite and tz_str == "Asia/Kolkata" and self._is_exchange_holiday(now):
            return False
        return True

    def _is_exchange_holiday(self, now: datetime) -> bool:
        """True on a weekday NSE holiday: once per IST day, after 09:20, NIFTY 50
        has no minute candles. Fail-OPEN: any error means 'open', never block
        trading on a real trading day because a probe failed."""
        day = now.date()
        mono = time.monotonic()
        if self._holiday and self._holiday[0] == day and not self._holiday[1]:
            return False                                   # saw candles today: open
        last_empty = self._holiday_empty[1] if self._holiday_empty and self._holiday_empty[0] == day else None
        is_holiday = bool(self._holiday and self._holiday[0] == day)
        if now.time() < HOLIDAY_PROBE_AFTER:
            return False
        # One empty answer could be late data, so a holiday needs a second empty
        # probe HOLIDAY_CONFIRM_S later, and is re-checked every HOLIDAY_RECHECK_S.
        wait = HOLIDAY_RECHECK_S if is_holiday else HOLIDAY_CONFIRM_S
        if last_empty is not None and mono - last_empty < wait:
            return is_holiday
        if mono - self._holiday_probe_failed_at < HOLIDAY_PROBE_RETRY_S:
            return is_holiday
        try:
            candles = self._call_with_retry(
                "historical_data(holiday probe)",
                lambda k: k.historical_data(
                    instrument_token=NIFTY_TOKEN, from_date=day.isoformat(),
                    to_date=day.isoformat(), interval="minute"),
            )
        except Exception as e:
            self._holiday_probe_failed_at = mono
            logger.warning(f"Holiday probe failed, assuming market open: {e}")
            return is_holiday
        if candles:
            if is_holiday:
                logger.warning(f"NIFTY candles appeared for {day}: market is open after all")
            self._holiday = (day, False)
            return False
        if last_empty is None:                             # first empty probe: wait to confirm
            self._holiday_empty = (day, mono)
            logger.warning(f"No NIFTY candles yet for {day}; re-checking in {HOLIDAY_CONFIRM_S // 60} min")
            return False
        self._holiday_empty = (day, mono)
        if not is_holiday:
            logger.warning(f"No NIFTY candles for {day} on two probes: treating as an exchange holiday")
        self._holiday = (day, True)
        return True
