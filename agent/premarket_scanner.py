"""
Pre-Market Scanner — Scans ALL NSE stocks before market open to build
a dynamic watchlist for the day's intraday scalping.

Runs at ~8:30 AM IST (before 9:15 open). Uses Kite Connect to:
1. Fetch previous close for all ~1800 NSE equities
2. Identify gap-ups, gap-downs, high-volume pre-market movers
3. Rank by tradability (liquidity, volatility, gap size)
4. Return top 20-30 stocks as today's scalping watchlist

The watchlist feeds into the agent's decision loop, replacing the
static 111-stock default with a focused, high-quality set.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# Minimum filters for scalping candidates
MIN_PRICE = 50            # Skip penny stocks
MAX_PRICE = 10000         # Skip ultra-expensive (Hermes, MRF)
MIN_AVG_VOLUME = 500_000  # Minimum 20-day avg volume for liquidity
MIN_GAP_PCT = 0.5         # Minimum 0.5% gap to be interesting
MIN_MARKET_CAP_PROXY = 100_000_000  # Rough filter: price * volume > 10Cr


class PreMarketScanner:
    """Scans all NSE stocks to build a dynamic scalping watchlist."""

    def __init__(self, kite_client):
        self.kite = kite_client
        self._instruments = None

    def _load_nse_equities(self) -> List[Dict]:
        """Load all NSE equity instruments."""
        if self._instruments is None:
            all_instruments = self.kite.instruments("NSE")
            # Filter to equities only (exclude indices, ETFs with specific segment)
            self._instruments = [
                i for i in all_instruments
                if i.get("instrument_type") == "EQ"
                and i.get("exchange") == "NSE"
                and i.get("last_price", 0) > 0
            ]
            logger.info(f"📊 Loaded {len(self._instruments)} NSE equities")
        return self._instruments

    def scan(self, max_stocks: int = 25) -> List[Dict[str, Any]]:
        """
        Run the pre-market scan and return top stocks for scalping.

        Returns list of dicts with:
        - ticker: str (with .NS suffix)
        - prev_close: float
        - open_price: float (pre-market or previous close)
        - gap_pct: float
        - volume: int
        - avg_volume: float
        - vol_ratio: float
        - score: float (composite ranking score)
        - reason: str (why this stock was selected)
        """
        logger.info("🔍 Starting pre-market scan of all NSE stocks...")

        equities = self._load_nse_equities()
        if not equities:
            logger.warning("No NSE equities loaded — using empty watchlist")
            return []

        # Batch fetch quotes for all equities
        # Kite allows 500 per call, so we need multiple batches
        all_quotes = {}
        symbols = [f"NSE:{i['tradingsymbol']}" for i in equities]

        for i in range(0, len(symbols), 500):
            batch = symbols[i:i+500]
            try:
                quotes = self.kite.quote(batch)
                all_quotes.update(quotes)
            except Exception as e:
                logger.warning(f"Quote batch {i//500} failed: {e}")

        logger.info(f"📊 Got quotes for {len(all_quotes)} instruments")

        # Score and rank each stock
        candidates = []
        for instrument in equities:
            sym = instrument["tradingsymbol"]
            kite_sym = f"NSE:{sym}"
            quote = all_quotes.get(kite_sym, {})

            if not quote:
                continue

            ltp = quote.get("last_price", 0)
            ohlc = quote.get("ohlc", {})
            prev_close = ohlc.get("close", 0)
            day_open = ohlc.get("open", 0) or prev_close
            volume = quote.get("volume", 0)
            avg_volume = quote.get("average_traded_quantity", 0) or 1

            if not prev_close or not ltp:
                continue

            # Basic filters
            if ltp < MIN_PRICE or ltp > MAX_PRICE:
                continue
            if avg_volume < MIN_AVG_VOLUME:
                continue

            # Gap calculation
            gap_pct = ((day_open - prev_close) / prev_close) * 100 if prev_close else 0
            change_pct = ((ltp - prev_close) / prev_close) * 100 if prev_close else 0

            # Volume ratio (today vs average)
            vol_ratio = volume / avg_volume if avg_volume > 0 else 0

            # Skip if gap is too small and no volume spike
            if abs(gap_pct) < MIN_GAP_PCT and vol_ratio < 1.5:
                continue

            # Composite score for ranking
            # Higher is better for scalping potential
            score = 0

            # Gap size (bigger gaps = more momentum)
            score += min(abs(gap_pct), 5) * 2  # Cap at 5% gap, weight 2x

            # Volume spike (institutional activity)
            score += min(vol_ratio, 5) * 1.5  # Cap at 5x, weight 1.5x

            # Liquidity bonus (higher avg volume = easier to trade)
            if avg_volume > 2_000_000:
                score += 2
            elif avg_volume > 1_000_000:
                score += 1

            # Price range bonus (₹100-2000 is ideal for scalping)
            if 100 <= ltp <= 2000:
                score += 1

            # Determine reason
            reasons = []
            if abs(gap_pct) >= 2:
                reasons.append(f"{'Gap-up' if gap_pct > 0 else 'Gap-down'} {gap_pct:+.1f}%")
            elif abs(gap_pct) >= 0.5:
                reasons.append(f"Small gap {gap_pct:+.1f}%")
            if vol_ratio >= 2:
                reasons.append(f"High volume {vol_ratio:.1f}x")
            elif vol_ratio >= 1.5:
                reasons.append(f"Above-avg volume {vol_ratio:.1f}x")
            if abs(change_pct) >= 3:
                reasons.append(f"Big move {change_pct:+.1f}%")

            candidates.append({
                "ticker": f"{sym}.NS",
                "tradingsymbol": sym,
                "prev_close": round(prev_close, 2),
                "open_price": round(day_open, 2),
                "ltp": round(ltp, 2),
                "gap_pct": round(gap_pct, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "avg_volume": int(avg_volume),
                "vol_ratio": round(vol_ratio, 2),
                "score": round(score, 2),
                "reason": " | ".join(reasons) if reasons else "Moderate activity",
            })

        # Sort by score descending, take top N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:max_stocks]

        logger.info(f"🎯 Pre-market scan complete: {len(candidates)} candidates → top {len(top)} selected")
        for i, s in enumerate(top[:5]):
            logger.info(f"  {i+1}. {s['ticker']:15s} gap={s['gap_pct']:+5.1f}% vol={s['vol_ratio']:.1f}x score={s['score']:.1f} — {s['reason']}")

        return top

    def get_watchlist_tickers(self, max_stocks: int = 25) -> List[str]:
        """Run scan and return just the ticker list (for session watchlist)."""
        results = self.scan(max_stocks)
        return [r["ticker"] for r in results]

    def get_scan_summary(self, max_stocks: int = 25) -> str:
        """Run scan and return a formatted summary for LLM context."""
        results = self.scan(max_stocks)
        if not results:
            return "Pre-market scan: No significant movers found."

        lines = [f"## Pre-Market Scan — Top {len(results)} Stocks for Today"]
        lines.append(f"{'Ticker':15s} {'Gap%':>6s} {'Vol':>5s} {'Score':>6s} Reason")
        lines.append("-" * 65)
        for s in results:
            lines.append(
                f"{s['ticker']:15s} {s['gap_pct']:>+5.1f}% {s['vol_ratio']:>4.1f}x {s['score']:>5.1f}  {s['reason']}"
            )
        return "\n".join(lines)
