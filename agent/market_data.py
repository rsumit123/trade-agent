"""
Market Data Module — fetches stock prices and basic technical indicators.
Uses yfinance for NSE/BSE data (free, no API key needed).
"""

import yfinance as yf
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class MarketData:
    """Fetch and cache market data for Indian stocks."""

    def __init__(self, watchlist: List[str]):
        self.watchlist = watchlist
        self._price_cache: Dict[str, float] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)

    def get_current_prices(self, tickers: List[str] = None) -> Dict[str, float]:
        """Get latest prices for watchlist or specific tickers."""
        tickers = tickers or self.watchlist

        # Return cache if fresh
        if (self._cache_time and
            datetime.now() - self._cache_time < self._cache_ttl and
            all(t in self._price_cache for t in tickers)):
            return {t: self._price_cache[t] for t in tickers}

        prices = {}
        try:
            # Batch download is faster
            data = yf.download(tickers, period="1d", interval="1m",
                             progress=False, threads=True)
            if not data.empty:
                for ticker in tickers:
                    try:
                        if len(tickers) == 1:
                            last_price = data["Close"].iloc[-1]
                        else:
                            last_price = data["Close"][ticker].iloc[-1]
                        if pd.notna(last_price):
                            prices[ticker] = round(float(last_price), 2)
                    except (KeyError, IndexError):
                        logger.warning(f"No price data for {ticker}")
        except Exception as e:
            logger.error(f"Error fetching prices: {e}")

        # Fallback: fetch individually for missing tickers
        for ticker in tickers:
            if ticker not in prices:
                try:
                    stock = yf.Ticker(ticker)
                    hist = stock.history(period="1d")
                    if not hist.empty:
                        prices[ticker] = round(float(hist["Close"].iloc[-1]), 2)
                except Exception as e:
                    logger.warning(f"Fallback fetch failed for {ticker}: {e}")

        self._price_cache.update(prices)
        self._cache_time = datetime.now()
        return prices

    def get_stock_context(self, ticker: str, days: int = 5) -> Dict[str, Any]:
        """Get richer context for a specific stock — used when LLM wants deeper info."""
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=f"{days}d", interval="1d")

            if hist.empty:
                return {"ticker": ticker, "error": "No data available"}

            current = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current
            change_pct = ((current - prev_close) / prev_close) * 100

            # Simple technical indicators
            closes = hist["Close"].values
            sma_5 = float(closes.mean()) if len(closes) >= 5 else None
            high_5d = float(hist["High"].max())
            low_5d = float(hist["Low"].min())
            avg_volume = int(hist["Volume"].mean())

            # Daily returns for volatility
            if len(closes) > 1:
                returns = pd.Series(closes).pct_change().dropna()
                volatility = float(returns.std() * 100)
            else:
                volatility = 0.0

            return {
                "ticker": ticker,
                "current_price": round(current, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "high_5d": round(high_5d, 2),
                "low_5d": round(low_5d, 2),
                "sma_5": round(sma_5, 2) if sma_5 else None,
                "avg_volume": avg_volume,
                "volatility_pct": round(volatility, 2),
                "price_vs_sma": "above" if sma_5 and current > sma_5 else "below",
            }
        except Exception as e:
            logger.error(f"Error getting context for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        """Quick summary of all watchlist stocks — fed to LLM for scanning."""
        prices = self.get_current_prices()
        summaries = []

        for ticker in self.watchlist:
            if ticker in prices:
                ctx = self.get_stock_context(ticker, days=5)
                summaries.append(ctx)

        # Sort by absolute change — most movement first
        summaries.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
        return summaries

    def get_intraday_data(self, ticker: str, interval: str = "5m") -> pd.DataFrame:
        """Get intraday candle data for technical analysis."""
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period="1d", interval=interval)
            return data
        except Exception as e:
            logger.error(f"Intraday data error for {ticker}: {e}")
            return pd.DataFrame()

    def is_market_open(self) -> bool:
        """Check if Indian market is currently open (rough check)."""
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        # Monday=0 ... Friday=4
        if now.weekday() > 4:
            return False
        market_open = now.replace(hour=9, minute=15, second=0)
        market_close = now.replace(hour=15, minute=30, second=0)
        return market_open <= now <= market_close
