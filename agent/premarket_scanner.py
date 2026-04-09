"""
Pre-Market Scanner — Scans ALL NSE stocks before market open to build
a dynamic watchlist for the day's intraday scalping.

Architecture:
1. ONE-TIME: Download full NSE instrument master → cache to disk as JSON
   (includes tradingsymbol, instrument_token, lot_size, etc.)
   Only re-downloads if cache is older than 7 days.
2. DAILY (8:30 AM): Fetch OHLC for all cached equities via Kite API
3. Rank by gap %, intraday range, sector momentum
4. Return top 30 stocks as today's dynamic watchlist

The cached file means no redundant instrument downloads.
The LLM then gets the top 30 with context, and can call get_stock_details()
on ANY ticker for deeper analysis.
"""

import json
import logging
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

MIN_PRICE = 50
MAX_PRICE = 10000
MIN_GAP_PCT = 0.3
INSTRUMENT_CACHE = Path("sessions/_nse_instruments.json")
CACHE_MAX_AGE_DAYS = 7


class PreMarketScanner:
    """Scans ALL NSE stocks to build a dynamic scalping watchlist."""

    def __init__(self, kite_client):
        self.kite = kite_client

    def _ensure_instrument_cache(self) -> List[Dict]:
        """Load instrument cache from disk, refresh if stale."""
        INSTRUMENT_CACHE.parent.mkdir(parents=True, exist_ok=True)

        # Check if cache exists and is recent
        if INSTRUMENT_CACHE.exists():
            try:
                data = json.loads(INSTRUMENT_CACHE.read_text())
                cached_date = data.get("date", "")
                if cached_date:
                    age = (datetime.now() - datetime.fromisoformat(cached_date)).days
                    if age < CACHE_MAX_AGE_DAYS:
                        instruments = data.get("instruments", [])
                        logger.info(f"📋 Using cached instrument list ({len(instruments)} stocks, {age}d old)")
                        return instruments
            except Exception:
                pass

        # Download fresh instrument list
        logger.info("📥 Downloading fresh NSE instrument list...")
        return self._refresh_instrument_cache()

    def _refresh_instrument_cache(self) -> List[Dict]:
        """Download all NSE instruments and cache to disk."""
        try:
            all_instruments = self.kite.instruments("NSE")
        except Exception as e:
            logger.error(f"Failed to download instruments: {e}")
            return []

        # Filter to tradable equities only
        SKIP_KEYWORDS = {"NIFTY", "GOLD", "SILVER", "LIQUID", "BEES", "CPSE", "ETF", "MIDCAP", "VIX", "BHARATBOND"}
        equities = []
        for i in all_instruments:
            sym = i.get("tradingsymbol", "")
            if not sym or i.get("instrument_type") != "EQ" or i.get("exchange") != "NSE":
                continue
            if any(skip in sym.upper() for skip in SKIP_KEYWORDS):
                continue
            # Skip bonds (digits in first 3 chars) and SME (-SM, -P1, etc.)
            if any(c.isdigit() for c in sym[:3]):
                continue
            if any(tag in sym for tag in ["-SM", "-P1", "-NZ", "-N9", "-NM", "-YY", "-N2", "-RE", "-PP"]):
                continue

            equities.append({
                "tradingsymbol": sym,
                "instrument_token": i.get("instrument_token"),
                "name": i.get("name", ""),
                "lot_size": i.get("lot_size", 1),
                "tick_size": i.get("tick_size", 0.05),
            })

        # Save to cache
        cache_data = {
            "date": datetime.now().isoformat(),
            "count": len(equities),
            "instruments": equities,
        }
        INSTRUMENT_CACHE.write_text(json.dumps(cache_data, indent=2))
        logger.info(f"📋 Cached {len(equities)} NSE equities to {INSTRUMENT_CACHE}")
        return equities

    def scan(self, max_stocks: int = 30) -> List[Dict[str, Any]]:
        """
        Scan ALL cached NSE equities and return top movers for scalping.

        Uses kite.ohlc() for bulk OHLC data. Batches with 1s delay
        between calls to avoid Cloudflare rate limits.
        """
        instruments = self._ensure_instrument_cache()
        if not instruments:
            logger.warning("No instruments available for scanning")
            return []

        symbols = [i["tradingsymbol"] for i in instruments]
        logger.info(f"🔍 Scanning {len(symbols)} NSE equities...")

        # Fetch OHLC in batches of 500 with rate limit handling
        all_data = {}
        kite_symbols = [f"NSE:{sym}" for sym in symbols]

        for i in range(0, len(kite_symbols), 500):
            batch = kite_symbols[i:i + 500]
            try:
                data = self.kite.ohlc(batch)
                all_data.update(data)
                logger.info(f"  Batch {i // 500 + 1}: got {len(data)} quotes")
            except Exception as e:
                logger.warning(f"  Batch {i // 500 + 1} failed: {e}")
                # Retry after delay
                _time.sleep(3)
                try:
                    data = self.kite.ohlc(batch)
                    all_data.update(data)
                    logger.info(f"  Batch {i // 500 + 1} retry: got {len(data)} quotes")
                except Exception:
                    logger.warning(f"  Batch {i // 500 + 1} retry also failed — skipping")
            # Rate limit: 1s between batches
            if i + 500 < len(kite_symbols):
                _time.sleep(1)

        logger.info(f"📊 Got data for {len(all_data)} / {len(symbols)} stocks")

        # Score and rank
        candidates = []
        for sym in symbols:
            kite_sym = f"NSE:{sym}"
            data = all_data.get(kite_sym, {})
            if not data:
                continue

            ltp = data.get("last_price", 0)
            ohlc = data.get("ohlc", {})
            prev_close = ohlc.get("close", 0)
            day_open = ohlc.get("open", 0) or prev_close
            day_high = ohlc.get("high", 0)
            day_low = ohlc.get("low", 0)

            if not prev_close or not ltp or ltp < MIN_PRICE or ltp > MAX_PRICE:
                continue

            gap_pct = ((day_open - prev_close) / prev_close) * 100 if prev_close else 0
            change_pct = ((ltp - prev_close) / prev_close) * 100 if prev_close else 0
            day_range_pct = ((day_high - day_low) / prev_close) * 100 if prev_close and day_high > day_low else 0

            # Skip if no movement at all
            if abs(gap_pct) < MIN_GAP_PCT and abs(change_pct) < 1.0 and day_range_pct < 1.0:
                continue

            # Score
            score = 0
            score += min(abs(gap_pct), 5) * 2.0
            score += min(abs(change_pct), 10) * 1.0
            score += min(day_range_pct, 8) * 0.5
            if 100 <= ltp <= 2000:
                score += 1.5
            elif 50 <= ltp <= 3000:
                score += 0.5

            reasons = []
            if abs(gap_pct) >= 2:
                reasons.append(f"{'Gap-up' if gap_pct > 0 else 'Gap-down'} {gap_pct:+.1f}%")
            elif abs(gap_pct) >= 0.5:
                reasons.append(f"Gap {gap_pct:+.1f}%")
            if abs(change_pct) >= 3:
                reasons.append(f"Big move {change_pct:+.1f}%")
            if day_range_pct >= 3:
                reasons.append(f"Wide range {day_range_pct:.1f}%")

            # Find the instrument name from cache
            inst = next((i for i in instruments if i["tradingsymbol"] == sym), {})

            candidates.append({
                "ticker": f"{sym}.NS",
                "tradingsymbol": sym,
                "name": inst.get("name", ""),
                "prev_close": round(prev_close, 2),
                "open_price": round(day_open, 2),
                "ltp": round(ltp, 2),
                "gap_pct": round(gap_pct, 2),
                "change_pct": round(change_pct, 2),
                "day_range_pct": round(day_range_pct, 2),
                "score": round(score, 2),
                "reason": " | ".join(reasons) if reasons else "Active",
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:max_stocks]

        logger.info(f"🎯 Scan: {len(candidates)} movers → top {len(top)} selected")
        for i, s in enumerate(top[:10]):
            logger.info(f"  {i + 1:2d}. {s['ticker']:15s} gap={s['gap_pct']:+5.1f}% chg={s['change_pct']:+5.1f}% — {s['reason']}")

        return top

    def get_watchlist_tickers(self, max_stocks: int = 30) -> List[str]:
        """Run scan and return just the ticker list."""
        return [r["ticker"] for r in self.scan(max_stocks)]

    def get_scan_summary(self, max_stocks: int = 30) -> str:
        """Run scan and return formatted summary for LLM context."""
        results = self.scan(max_stocks)
        if not results:
            return "Pre-market scan: No significant movers found."

        lines = [f"## Pre-Market Scan — Top {len(results)} Stocks for Today"]
        lines.append(f"{'Ticker':15s} {'Name':20s} {'LTP':>8s} {'Gap%':>7s} {'Chg%':>7s} {'Range%':>7s} {'Score':>6s} Reason")
        lines.append("-" * 95)
        for s in results:
            name = (s.get("name", "") or "")[:20]
            lines.append(
                f"{s['ticker']:15s} {name:20s} {s['ltp']:>8.1f} {s['gap_pct']:>+6.1f}% {s['change_pct']:>+6.1f}% "
                f"{s['day_range_pct']:>6.1f}% {s['score']:>5.1f}  {s['reason']}"
            )
        return "\n".join(lines)
