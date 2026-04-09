"""
Backtest Market Data — Replays historical candle data for backtesting.

Drop-in replacement for MarketData/KiteMarketData. Instead of fetching
live prices, it reads from pre-loaded DataFrames of historical candles
and returns data up to the current simulation timestamp.

The caller advances time via set_time() and all methods return
data as if "now" is that timestamp.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BacktestMarketData:
    """Historical candle replay for backtesting. Same interface as MarketData."""

    def __init__(
        self,
        watchlist: List[str],
        market_preset=None,
        intraday_candles: Dict[str, pd.DataFrame] = None,
        daily_candles: Dict[str, pd.DataFrame] = None,
        current_time: datetime = None,
    ):
        """
        Args:
            watchlist: List of ticker symbols
            market_preset: MarketPreset for timezone/hours
            intraday_candles: {ticker: DataFrame[date, open, high, low, close, volume]}
                              Pre-fetched 15-min candles for the backtest day
            daily_candles: {ticker: DataFrame[date, open, high, low, close, volume]}
                          Pre-fetched daily candles for RSI/ATR computation (30+ days)
            current_time: The simulated "now" — advances via set_time()
        """
        self.watchlist = watchlist
        self.market_preset = market_preset
        self.intraday_candles = intraday_candles or {}
        self.daily_candles = daily_candles or {}
        self.current_time = current_time or datetime.now()
        self._price_cache: Dict[str, float] = {}

    def set_time(self, timestamp: datetime):
        """Advance the simulation clock. All subsequent calls use this time."""
        # Strip timezone for consistent comparison with candle dates
        if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo:
            self.current_time = timestamp.replace(tzinfo=None)
        else:
            self.current_time = timestamp
        self._price_cache = {}  # Clear cache on time change

    def set_intraday_candles(self, candles: Dict[str, pd.DataFrame]):
        """Replace intraday candles (called at start of each new backtest day)."""
        self.intraday_candles = candles
        self._price_cache = {}

    # ── Core Interface (same as MarketData / KiteMarketData) ──

    def get_current_prices(self, tickers: List[str] = None) -> Dict[str, float]:
        """Return close price at the current simulation time."""
        if self._price_cache and not tickers:
            return self._price_cache

        symbols = tickers or self.watchlist
        prices = {}

        for ticker in symbols:
            candles = self.intraday_candles.get(ticker)
            if candles is None or candles.empty:
                logger.debug(f"  get_current_prices: no candles for {ticker} "
                            f"(available keys: {list(self.intraday_candles.keys())[:5]}...)")
                continue

            # Strip timezone from candle dates for comparison
            candle_dates = candles["date"].apply(
                lambda x: x.replace(tzinfo=None) if hasattr(x, 'tzinfo') and x.tzinfo else x
            )

            # Get the latest candle at or before current_time
            mask = candle_dates <= self.current_time
            valid = candles[mask]
            if valid.empty:
                prices[ticker] = float(candles.iloc[0]["open"])
            else:
                prices[ticker] = float(valid.iloc[-1]["close"])

        if not tickers:
            self._price_cache = prices
        return prices

    def get_stock_context(self, ticker: str, include_vwap: bool = True) -> Dict[str, Any]:
        """Compute technical context from historical candles up to current_time."""
        try:
            # Get daily candles for indicators (RSI, ATR, SMA)
            daily = self.daily_candles.get(ticker, pd.DataFrame())
            intraday = self.intraday_candles.get(ticker, pd.DataFrame())

            # Current price from intraday
            prices = self.get_current_prices([ticker])
            current_price = prices.get(ticker, 0)
            if not current_price:
                return self._empty_context(ticker)

            # Previous close from daily
            if len(daily) >= 2:
                prev_close = float(daily.iloc[-2]["close"])  # -2 because -1 is today
            elif len(daily) >= 1:
                prev_close = float(daily.iloc[-1]["close"])
            else:
                prev_close = current_price

            change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

            # Compute technicals from daily candles
            rsi_14 = None
            atr_14 = None
            sma_5 = None
            high_5d = current_price
            low_5d = current_price
            vol_ratio = None

            if len(daily) >= 15:
                close = daily["close"].astype(float)

                # RSI(14)
                delta = close.diff()
                gain = delta.where(delta > 0, 0.0)
                loss = (-delta).where(delta < 0, 0.0)
                avg_gain = gain.ewm(com=13, min_periods=14).mean()
                avg_loss = loss.ewm(com=13, min_periods=14).mean()
                rs = avg_gain / avg_loss.replace(0, float('inf'))
                rsi = 100 - (100 / (1 + rs))
                rsi_14 = round(float(rsi.iloc[-1]), 1)

                # ATR(14)
                h = daily["high"].astype(float)
                l = daily["low"].astype(float)
                c = close.shift(1)
                tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
                atr = tr.ewm(com=13, min_periods=14).mean()
                atr_14 = round(float(atr.iloc[-1]), 2)

                # SMA(5)
                sma_5 = round(float(close.rolling(5).mean().iloc[-1]), 2)

                # 5-day high/low
                high_5d = float(daily["high"].tail(5).max())
                low_5d = float(daily["low"].tail(5).min())

                # Volume ratio
                if "volume" in daily.columns:
                    avg_vol = float(daily["volume"].tail(20).mean())
                    today_vol = float(daily["volume"].iloc[-1]) if len(daily) > 0 else 0
                    if avg_vol > 0:
                        vol_ratio = round(today_vol / avg_vol, 2)

            # Distance to support/resistance
            dist_res = round(((high_5d - current_price) / current_price * 100), 2) if current_price else None
            dist_sup = round(((current_price - low_5d) / current_price * 100), 2) if current_price else None

            # VWAP from intraday candles up to current_time
            vwap = None
            if include_vwap and not intraday.empty:
                intraday_dates = intraday["date"].apply(
                    lambda x: x.replace(tzinfo=None) if hasattr(x, 'tzinfo') and x.tzinfo else x
                )
                valid = intraday[intraday_dates <= self.current_time]
                if not valid.empty and "volume" in valid.columns:
                    typical_price = (valid["high"] + valid["low"] + valid["close"]) / 3
                    vol = valid["volume"]
                    if vol.sum() > 0:
                        vwap = round(float((typical_price * vol).sum() / vol.sum()), 2)

            result = {
                "ticker": ticker,
                "current_price": round(current_price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "high_5d": round(high_5d, 2),
                "low_5d": round(low_5d, 2),
                "dist_to_resistance_pct": dist_res,
                "dist_to_support_pct": dist_sup,
                "sma_5": sma_5,
                "price_vs_sma": "above" if (sma_5 and current_price > sma_5) else "below",
                "rsi_14": rsi_14,
                "atr_14": atr_14,
                "vol_ratio": vol_ratio,
            }
            if include_vwap and vwap:
                result["vwap"] = vwap
                result["price_vs_vwap"] = "above" if current_price > vwap else "below"
            return result

        except Exception as e:
            logger.error(f"BacktestMarketData.get_stock_context failed for {ticker}: {e}")
            return self._empty_context(ticker)

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        """Return summary for all watchlist stocks at current simulation time."""
        prices = self.get_current_prices()
        summaries = []

        if not prices:
            logger.warning(f"  get_watchlist_summary: NO prices returned for {len(self.watchlist)} watchlist tickers "
                          f"at time {self.current_time}. "
                          f"Intraday candle keys: {list(self.intraday_candles.keys())[:5]}")

        for ticker in self.watchlist:
            price = prices.get(ticker, 0)
            if not price:
                continue

            daily = self.daily_candles.get(ticker, pd.DataFrame())
            prev_close = float(daily.iloc[-2]["close"]) if len(daily) >= 2 else price
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

            summaries.append({
                "ticker": ticker,
                "current_price": round(price, 2),
                "change_pct": round(change_pct, 2),
                "rsi_14": None,  # Computed on-demand via get_stock_context
                "atr_14": None,
                "vol_ratio": None,
                "dist_to_resistance_pct": None,
                "dist_to_support_pct": None,
                "sma_5": None,
                "price_vs_sma": "unknown",
            })

        summaries.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
        return summaries

    def get_intraday_data(self, ticker: str, interval: str = "5minute") -> pd.DataFrame:
        """Return intraday candles up to current simulation time."""
        candles = self.intraday_candles.get(ticker, pd.DataFrame())
        if candles.empty:
            return candles
        candle_dates = candles["date"].apply(
            lambda x: x.replace(tzinfo=None) if hasattr(x, 'tzinfo') and x.tzinfo else x
        )
        return candles[candle_dates <= self.current_time].copy()

    def is_market_open(self) -> bool:
        """Check if current simulation time is within market hours."""
        if self.market_preset and self.market_preset.is_24x7:
            return True

        if not self.market_preset:
            return True  # Default to open during backtest

        open_str = self.market_preset.market_open or "09:15"
        close_str = self.market_preset.market_close or "15:30"

        open_h, open_m = map(int, open_str.split(":"))
        close_h, close_m = map(int, close_str.split(":"))

        ct = self.current_time
        market_open = ct.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        market_close = ct.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

        return market_open <= ct <= market_close

    def _empty_context(self, ticker: str) -> Dict[str, Any]:
        """Return empty context for a ticker with no data."""
        return {
            "ticker": ticker, "current_price": 0, "prev_close": 0,
            "change_pct": 0, "rsi_14": None, "atr_14": None,
            "vol_ratio": None, "dist_to_resistance_pct": None,
            "dist_to_support_pct": None, "sma_5": None,
            "price_vs_sma": "unknown",
        }
