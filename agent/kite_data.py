"""
Kite Market Data — Real-time market data provider using Zerodha Kite Connect API.

Drop-in replacement for MarketData (yfinance). Same interface, real-time data.
Uses kite.quote() for live prices, kite.historical_data() for candles/technicals.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class KiteMarketData:
    """Market data provider using Zerodha Kite Connect API."""

    def __init__(self, watchlist: List[str], market_preset=None, kite_client=None, auth=None):
        self.watchlist = watchlist
        self.market_preset = market_preset
        self.kite = kite_client
        self._auth = auth  # KiteAuth handle so we can re-issue a token if Kite revokes
        self._instrument_map: Dict[str, int] = {}  # tradingsymbol → instrument_token
        self._price_cache: Dict[str, float] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=30)  # 30s cache (much fresher than yfinance 5min)

        if self.kite:
            self._load_instruments()

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return ("incorrect" in msg and ("api_key" in msg or "access_token" in msg)) \
            or "tokenexception" in msg or "token expired" in msg

    def _refresh_kite(self) -> bool:
        """Force a re-auth and swap in the new client. Returns True on success."""
        if not self._auth:
            return False
        try:
            # Force a fresh token (delete the cached one — it's clearly stale)
            try:
                if hasattr(self._auth, "token_path") and self._auth.token_path.exists():
                    self._auth.token_path.unlink()
            except Exception:
                pass
            new_kite = self._auth.get_authenticated_client()
            self.kite = new_kite
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

    def _load_instruments(self):
        """Cache instrument tokens for quick lookup."""
        try:
            instruments = self.kite.instruments("NSE")
            for i in instruments:
                sym = i["tradingsymbol"]
                self._instrument_map[sym] = i["instrument_token"]
                # Also map with .NS suffix for compatibility
                self._instrument_map[f"{sym}.NS"] = i["instrument_token"]
            logger.info(f"📊 Loaded {len(instruments)} NSE instruments from Kite")
        except Exception as e:
            logger.error(f"Failed to load Kite instruments: {e}")

    def _strip_ns(self, ticker: str) -> str:
        """Remove .NS suffix: 'RELIANCE.NS' → 'RELIANCE'"""
        return ticker.replace(".NS", "")

    def _kite_symbol(self, ticker: str) -> str:
        """Convert to Kite format: 'RELIANCE.NS' → 'NSE:RELIANCE'"""
        return f"NSE:{self._strip_ns(ticker)}"

    def _get_instrument_token(self, ticker: str) -> Optional[int]:
        """Get instrument token for a ticker."""
        return self._instrument_map.get(ticker) or self._instrument_map.get(self._strip_ns(ticker))

    # ── Core Data Methods (same interface as MarketData) ─────

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
            return self._price_cache or {}

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
            current_price = quote.get("last_price", 0)
            ohlc = quote.get("ohlc", {})
            prev_close = ohlc.get("close", current_price)
            day_open = ohlc.get("open", current_price)
            day_high = ohlc.get("high", current_price)
            day_low = ohlc.get("low", current_price)
            volume = quote.get("volume", 0)

            change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

            # Get historical candles for technical indicators (30 days daily)
            if token:
                from_date = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
                to_date = datetime.now().strftime("%Y-%m-%d")
                candles = self._call_with_retry(
                    "historical_data(stock_context)",
                    lambda k, t=token, fd=from_date, td=to_date: k.historical_data(
                        instrument_token=t, from_date=fd, to_date=td, interval="day",
                    ),
                )
                df = pd.DataFrame(candles)
            else:
                df = pd.DataFrame()

            # Compute technicals from historical data
            rsi_14 = None
            atr_14 = None
            sma_5 = None
            ema_20 = None
            ema_50 = None
            macd_line = None
            macd_signal = None
            macd_hist = None
            vol_ratio = None
            high_5d = current_price
            low_5d = current_price

            if len(df) >= 15:
                close = df["close"]

                # RSI(14) — Wilder smoothing
                delta = close.diff()
                gain = delta.where(delta > 0, 0.0)
                loss = (-delta).where(delta < 0, 0.0)
                avg_gain = gain.ewm(com=13, min_periods=14).mean()
                avg_loss = loss.ewm(com=13, min_periods=14).mean()
                rs = avg_gain / avg_loss.replace(0, float('inf'))
                rsi = 100 - (100 / (1 + rs))
                rsi_14 = round(float(rsi.iloc[-1]), 1)

                # ATR(14)
                h = df["high"]
                l = df["low"]
                c = close.shift(1)
                tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
                atr = tr.ewm(com=13, min_periods=14).mean()
                atr_14 = round(float(atr.iloc[-1]), 2)

                # SMA(5)
                sma_5 = round(float(close.rolling(5).mean().iloc[-1]), 2)

                # EMA(20) and EMA(50) — trend filters
                if len(df) >= 20:
                    ema_20 = round(float(close.ewm(span=20, adjust=False).mean().iloc[-1]), 2)
                if len(df) >= 50:
                    ema_50 = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)

                # MACD(12, 26, 9) — trend & momentum
                if len(df) >= 26:
                    ema_12 = close.ewm(span=12, adjust=False).mean()
                    ema_26 = close.ewm(span=26, adjust=False).mean()
                    macd = ema_12 - ema_26
                    signal = macd.ewm(span=9, adjust=False).mean()
                    macd_line = round(float(macd.iloc[-1]), 2)
                    macd_signal = round(float(signal.iloc[-1]), 2)
                    macd_hist = round(macd_line - macd_signal, 2)

                # 5-day high/low
                high_5d = float(df["high"].tail(5).max())
                low_5d = float(df["low"].tail(5).min())

                # Volume ratio: today's volume / 20-day average
                avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else float(df["volume"].mean())
                if avg_vol > 0:
                    vol_ratio = round(volume / avg_vol, 2)

            # Distance to resistance/support
            dist_to_resistance = round(((high_5d - current_price) / current_price * 100), 2) if current_price else None
            dist_to_support = round(((current_price - low_5d) / current_price * 100), 2) if current_price else None

            # VWAP from Kite quote (built-in, no extra call needed!)
            vwap = quote.get("average_price", None)  # Kite provides VWAP as average_price

            # Trend assessment from EMA20/50
            ema_trend = None
            if ema_20 and ema_50:
                ema_trend = "bullish" if ema_20 > ema_50 else "bearish"
            price_vs_ema20 = "above" if (ema_20 and current_price > ema_20) else "below" if ema_20 else None

            # MACD signal
            macd_state = None
            if macd_line is not None and macd_signal is not None:
                if macd_line > macd_signal and macd_hist > 0:
                    macd_state = "bullish_cross"
                elif macd_line < macd_signal and macd_hist < 0:
                    macd_state = "bearish_cross"
                else:
                    macd_state = "neutral"

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
                "price_vs_sma": "above" if (sma_5 and current_price > sma_5) else "below",
                "ema_20": ema_20,
                "ema_50": ema_50,
                "ema_trend": ema_trend,
                "price_vs_ema20": price_vs_ema20,
                "macd": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
                "macd_state": macd_state,
                "avg_volume": int(volume),
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
            return {
                "ticker": ticker, "current_price": 0, "prev_close": 0,
                "change_pct": 0, "rsi_14": None, "atr_14": None,
                "vol_ratio": None, "dist_to_resistance_pct": None,
                "dist_to_support_pct": None, "sma_5": None,
                "price_vs_sma": "unknown",
            }

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
                volume = quote.get("volume", 0)
                vwap = quote.get("average_price")

                # For the summary, we need RSI and ATR from historical data
                # But fetching 111 historical calls is slow — use get_stock_context for that
                # Instead, provide what we can from the quote and mark RSI/ATR as None
                # The LLM can call get_stock_details for individual stocks it's interested in

                summaries.append({
                    "ticker": ticker,
                    "current_price": round(current_price, 2),
                    "change_pct": round(change_pct, 2),
                    "rsi_14": None,  # Computed on-demand via get_stock_details
                    "atr_14": None,
                    "vol_ratio": None,
                    "dist_to_resistance_pct": None,
                    "dist_to_support_pct": None,
                    "sma_5": None,
                    "price_vs_sma": "above" if (vwap and current_price > vwap) else "below",
                    "vwap": round(vwap, 2) if vwap else None,
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
        return market_open <= now <= market_close
