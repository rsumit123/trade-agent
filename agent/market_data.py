"""
Market Data Module — fetches prices and technical indicators via yfinance.
Works for both NSE stocks (.NS tickers) and crypto (BTC-USD, ETH-USD, etc.).

Indicators computed:
  - RSI(14)         — momentum oscillator, overbought >70 / oversold <30
  - ATR(14)         — average true range, used for stop/target sizing
  - Volume ratio    — today's volume vs 20-day avg (breakout confirmation)
  - VWAP            — intraday volume-weighted avg price (on-demand only)
  - SMA(5)          — 5-day simple moving average
  - Dist to S/R     — % distance to 5-day support / resistance
"""

import yfinance as yf
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class MarketData:
    """Fetch and cache market data.  Works for stocks and crypto."""

    def __init__(self, watchlist: List[str], market_preset=None):
        self.watchlist = watchlist
        self.market_preset = market_preset
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

    def get_stock_context(self, ticker: str, include_vwap: bool = True) -> Dict[str, Any]:
        """
        Get rich technical context for a specific stock.
        Fetches 30 days of daily data to compute ATR(14), RSI(14), vol_ratio(20d).
        VWAP requires a separate 1m intraday call — set include_vwap=False for
        bulk watchlist scans to avoid 111 extra network calls per cycle.
        """
        try:
            stock = yf.Ticker(ticker)
            # 30 days gives us enough for ATR(14), RSI(14), vol_ratio(20d)
            hist = stock.history(period="30d", interval="1d")

            if hist.empty:
                return {"ticker": ticker, "error": "No data available"}

            if len(hist) < 5:
                return {"ticker": ticker, "error": "Insufficient history"}

            current = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current
            change_pct = ((current - prev_close) / prev_close) * 100

            # ── SMA(5) ──────────────────────────────────────────────────
            closes = hist["Close"]
            sma_5 = float(closes.iloc[-5:].mean()) if len(hist) >= 5 else None

            # ── 5-day high/low → distance to S/R ──────────────────────
            high_5d = float(hist["High"].iloc[-5:].max())
            low_5d  = float(hist["Low"].iloc[-5:].min())
            dist_to_resistance_pct = round(((high_5d - current) / current) * 100, 2) if current > 0 else 0
            dist_to_support_pct    = round(((current - low_5d) / current) * 100, 2) if current > 0 else 0

            # ── ATR(14) — Wilder smoothing ─────────────────────────────
            atr_14 = None
            if len(hist) >= 15:
                high  = hist["High"]
                low   = hist["Low"]
                pclose = hist["Close"].shift(1)
                tr = pd.concat([
                    high - low,
                    (high - pclose).abs(),
                    (low  - pclose).abs(),
                ], axis=1).max(axis=1)
                # ewm(com=13) = Wilder smoothing with alpha=1/14
                atr_14 = round(float(tr.ewm(com=13, adjust=False).mean().iloc[-1]), 2)

            # ── RSI(14) — Wilder smoothing ─────────────────────────────
            rsi_14 = None
            if len(hist) >= 15:
                delta    = closes.diff()
                gain     = delta.clip(lower=0)
                loss     = (-delta).clip(lower=0)
                avg_gain = gain.ewm(com=13, adjust=False).mean()
                avg_loss = loss.ewm(com=13, adjust=False).mean()
                rs       = avg_gain / avg_loss.replace(0, float("nan"))
                rsi_series = 100 - (100 / (1 + rs))
                rsi_14 = round(float(rsi_series.iloc[-1]), 1)

            # ── EMA(20), EMA(50) — trend filters ───────────────────────
            ema_20 = None
            ema_50 = None
            if len(hist) >= 20:
                ema_20 = round(float(closes.ewm(span=20, adjust=False).mean().iloc[-1]), 2)
            if len(hist) >= 50:
                ema_50 = round(float(closes.ewm(span=50, adjust=False).mean().iloc[-1]), 2)

            # ── MACD(12, 26, 9) ────────────────────────────────────────
            macd_line = macd_signal = macd_hist = None
            if len(hist) >= 26:
                ema_12 = closes.ewm(span=12, adjust=False).mean()
                ema_26 = closes.ewm(span=26, adjust=False).mean()
                macd = ema_12 - ema_26
                signal = macd.ewm(span=9, adjust=False).mean()
                macd_line = round(float(macd.iloc[-1]), 2)
                macd_signal = round(float(signal.iloc[-1]), 2)
                macd_hist = round(macd_line - macd_signal, 2)

            # ── Volume ratio (today vs 20-day avg) ─────────────────────
            vol_ratio = None
            avg_volume = int(hist["Volume"].mean())
            if len(hist) >= 2:
                vol_today   = float(hist["Volume"].iloc[-1])
                # 20 sessions before today (or however many we have)
                hist_window = hist["Volume"].iloc[max(-21, -len(hist)):-1]
                vol_20d_avg = float(hist_window.mean()) if len(hist_window) > 0 else 0
                if vol_20d_avg > 0:
                    vol_ratio = round(vol_today / vol_20d_avg, 2)

            # ── Daily returns for historical volatility ────────────────
            if len(closes) > 1:
                returns    = closes.pct_change().dropna()
                volatility = round(float(returns.std() * 100), 2)
            else:
                volatility = 0.0

            # ── VWAP (intraday 1m candles) — only when requested ───────
            vwap         = None
            price_vs_vwap = "unknown"
            if include_vwap:
                try:
                    intraday = self.get_intraday_data(ticker, interval="1m")
                    if not intraday.empty and len(intraday) > 1:
                        tp  = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3
                        cum_tpv = (tp * intraday["Volume"]).cumsum()
                        cum_vol = intraday["Volume"].cumsum()
                        vwap_series = cum_tpv / cum_vol.replace(0, float("nan"))
                        vwap = round(float(vwap_series.iloc[-1]), 2)
                        price_vs_vwap = "above" if current > vwap else "below"
                except Exception as e:
                    logger.debug(f"VWAP calc failed for {ticker}: {e}")

            ema_trend = None
            if ema_20 and ema_50:
                ema_trend = "bullish" if ema_20 > ema_50 else "bearish"
            macd_state = None
            if macd_line is not None and macd_signal is not None:
                if macd_line > macd_signal and macd_hist > 0:
                    macd_state = "bullish_cross"
                elif macd_line < macd_signal and macd_hist < 0:
                    macd_state = "bearish_cross"
                else:
                    macd_state = "neutral"

            return {
                "ticker":                  ticker,
                "current_price":           round(current, 2),
                "prev_close":              round(prev_close, 2),
                "change_pct":              round(change_pct, 2),
                "high_5d":                 round(high_5d, 2),
                "low_5d":                  round(low_5d, 2),
                "dist_to_resistance_pct":  dist_to_resistance_pct,
                "dist_to_support_pct":     dist_to_support_pct,
                "sma_5":                   round(sma_5, 2) if sma_5 else None,
                "price_vs_sma":            "above" if sma_5 and current > sma_5 else "below",
                "ema_20":                  ema_20,
                "ema_50":                  ema_50,
                "ema_trend":               ema_trend,
                "price_vs_ema20":          ("above" if (ema_20 and current > ema_20) else "below" if ema_20 else None),
                "macd":                    macd_line,
                "macd_signal":             macd_signal,
                "macd_hist":               macd_hist,
                "macd_state":              macd_state,
                "avg_volume":              avg_volume,
                "volatility_pct":          volatility,
                "rsi_14":                  rsi_14,
                "atr_14":                  atr_14,
                "vol_ratio":               vol_ratio,
                "vwap":                    vwap,
                "price_vs_vwap":           price_vs_vwap,
            }
        except Exception as e:
            logger.error(f"Error getting context for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e)}

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        """
        Quick summary of all watchlist stocks — fed to LLM for scanning.
        Skips VWAP (too slow for 111 tickers) — LLM can call get_stock_details
        for VWAP on any specific stock it wants to trade.
        """
        prices = self.get_current_prices()
        summaries = []

        for ticker in self.watchlist:
            if ticker in prices:
                # include_vwap=False → no 1m intraday call per ticker
                ctx = self.get_stock_context(ticker, include_vwap=False)
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
        """Check if the market is currently open.  24/7 markets always return True."""
        if self.market_preset and self.market_preset.is_24x7:
            return True

        from zoneinfo import ZoneInfo

        tz_str = self.market_preset.timezone if self.market_preset else "Asia/Kolkata"
        open_str = self.market_preset.market_open if self.market_preset else "09:15"
        close_str = self.market_preset.market_close if self.market_preset else "15:30"

        now = datetime.now(ZoneInfo(tz_str))
        # Monday=0 ... Friday=4
        if now.weekday() > 4:
            return False

        open_h, open_m = map(int, open_str.split(":"))
        close_h, close_m = map(int, close_str.split(":"))
        market_open = now.replace(hour=open_h, minute=open_m, second=0)
        market_close = now.replace(hour=close_h, minute=close_m, second=0)
        return market_open <= now <= market_close


def create_market_data(config, market_preset=None):
    """Factory: create appropriate market data provider based on config.

    Returns KiteMarketData if data_source='kite' and Kite credentials are available,
    otherwise falls back to yfinance MarketData.
    """
    data_source = getattr(config, 'data_source', 'yfinance')
    if hasattr(config, '_session_config') and config._session_config:
        data_source = getattr(config._session_config, 'data_source', data_source)

    if data_source == "kite":
        try:
            from .kite_auth import KiteAuth
            from .kite_data import KiteMarketData

            auth = KiteAuth()  # Reads from env vars
            kite = auth.get_authenticated_client()
            logger.info("📊 Using Kite Connect for market data (real-time)")
            return KiteMarketData(config.watchlist, market_preset, kite, auth=auth)
        except Exception as e:
            logger.warning(f"Kite Connect init failed, falling back to yfinance: {e}")
            return MarketData(config.watchlist, market_preset)

    return MarketData(config.watchlist, market_preset)
