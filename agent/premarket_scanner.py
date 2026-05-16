"""
Pre-Market Scanner — Two-phase intelligent stock picker for intraday scalping.

Phase 1 (Quantitative): Scan ALL ~3000 NSE equities via Kite OHLC,
    filter by price/gap/range to ~50-80 candidates.

Phase 2 (LLM Intelligence): Give candidates to the LLM with market news
    and distilled rules. LLM picks the final 25-30 with reasoning.
    Stock picks are tracked and fed back into learning — the agent
    learns WHAT to trade, not just HOW.

Instrument master is cached to disk (weekly refresh).
"""

import json
import logging
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

MIN_PRICE = 50
MAX_PRICE = 10000
MIN_GAP_PCT = 0.3
INSTRUMENT_CACHE = Path("sessions/_nse_instruments.json")
CACHE_MAX_AGE_DAYS = 7


class PreMarketScanner:
    """Two-phase stock picker: quantitative filter + LLM intelligence."""

    def __init__(self, kite_client, auth=None):
        self.kite = kite_client
        self._auth = auth  # KiteAuth handle for token-refresh-on-401

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            ("incorrect" in msg and ("api_key" in msg or "access_token" in msg))
            or "tokenexception" in msg
            or "token expired" in msg
            or "just a moment" in msg  # Kite returning Cloudflare challenge on stale auth
        )

    def _refresh_kite(self) -> bool:
        if not self._auth:
            return False
        try:
            try:
                if hasattr(self._auth, "token_path") and self._auth.token_path.exists():
                    self._auth.token_path.unlink()
            except Exception:
                pass
            self.kite = self._auth.get_authenticated_client()
            logger.info("🔑 Scanner re-authenticated Kite client")
            return True
        except Exception as e:
            logger.error(f"Scanner re-auth failed: {e}")
            return False

    def _call_with_retry(self, label: str, fn):
        try:
            return fn(self.kite)
        except Exception as e:
            if self._is_auth_error(e) and self._refresh_kite():
                return fn(self.kite)
            raise

    # ── Phase 0: Instrument Cache ─────────────────────────────

    def _ensure_instrument_cache(self) -> List[Dict]:
        """Load cached instruments from disk, refresh if stale."""
        INSTRUMENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        if INSTRUMENT_CACHE.exists():
            try:
                data = json.loads(INSTRUMENT_CACHE.read_text())
                age = (datetime.now() - datetime.fromisoformat(data.get("date", "2000-01-01"))).days
                if age < CACHE_MAX_AGE_DAYS:
                    instruments = data.get("instruments", [])
                    logger.info(f"📋 Using cached instruments ({len(instruments)} stocks, {age}d old)")
                    return instruments
            except Exception:
                pass
        return self._refresh_instrument_cache()

    def _refresh_instrument_cache(self) -> List[Dict]:
        """Download all NSE instruments and cache to disk."""
        try:
            all_instruments = self.kite.instruments("NSE")
        except Exception as e:
            logger.error(f"Failed to download instruments: {e}")
            return []

        SKIP_KEYWORDS = {"NIFTY", "GOLD", "SILVER", "LIQUID", "BEES", "CPSE", "ETF", "MIDCAP", "VIX", "BHARATBOND"}
        equities = []
        for i in all_instruments:
            sym = i.get("tradingsymbol", "")
            if not sym or i.get("instrument_type") != "EQ" or i.get("exchange") != "NSE":
                continue
            if any(skip in sym.upper() for skip in SKIP_KEYWORDS):
                continue
            if any(c.isdigit() for c in sym[:3]):
                continue
            if any(tag in sym for tag in ["-SM", "-P1", "-NZ", "-N9", "-NM", "-YY", "-N2", "-RE", "-PP", "-ST", "-BE"]):
                continue
            equities.append({
                "tradingsymbol": sym,
                "instrument_token": i.get("instrument_token"),
                "name": i.get("name", ""),
            })

        cache_data = {"date": datetime.now().isoformat(), "count": len(equities), "instruments": equities}
        INSTRUMENT_CACHE.write_text(json.dumps(cache_data, indent=2))
        logger.info(f"📋 Cached {len(equities)} NSE equities")
        return equities

    # ── Phase 1: Quantitative Filter ──────────────────────────

    def scan_phase1(self, max_candidates: int = 60) -> List[Dict[str, Any]]:
        """
        Phase 1: Fetch OHLC for all stocks, filter and rank quantitatively.
        Returns ~50-80 candidates for LLM to review.
        """
        instruments = self._ensure_instrument_cache()
        if not instruments:
            return []

        symbols = [i["tradingsymbol"] for i in instruments]
        logger.info(f"🔍 Phase 1: Scanning {len(symbols)} NSE equities...")

        # Fetch OHLC in batches
        all_data = {}
        kite_symbols = [f"NSE:{sym}" for sym in symbols]
        for i in range(0, len(kite_symbols), 500):
            batch = kite_symbols[i:i + 500]
            try:
                data = self._call_with_retry(
                    f"ohlc(batch{i // 500 + 1})",
                    lambda k, b=batch: k.ohlc(b),
                )
                all_data.update(data)
            except Exception as e:
                logger.warning(f"  Batch {i // 500 + 1} failed: {e}")
                _time.sleep(3)
                try:
                    data = self._call_with_retry(
                        f"ohlc(batch{i // 500 + 1} retry)",
                        lambda k, b=batch: k.ohlc(b),
                    )
                    all_data.update(data)
                except Exception:
                    pass
            if i + 500 < len(kite_symbols):
                _time.sleep(1)

        logger.info(f"📊 Got data for {len(all_data)} stocks")

        # Build instrument name lookup
        name_map = {i["tradingsymbol"]: i.get("name", "") for i in instruments}

        # Score and filter
        candidates = []
        for sym in symbols:
            data = all_data.get(f"NSE:{sym}", {})
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

            if abs(gap_pct) < MIN_GAP_PCT and abs(change_pct) < 1.0 and day_range_pct < 1.0:
                continue

            # Simple quantitative score for initial ranking
            score = min(abs(gap_pct), 5) * 2 + min(abs(change_pct), 10) * 1 + min(day_range_pct, 8) * 0.5
            if 100 <= ltp <= 2000:
                score += 1.5

            reasons = []
            if abs(gap_pct) >= 2:
                reasons.append(f"{'Gap-up' if gap_pct > 0 else 'Gap-down'} {gap_pct:+.1f}%")
            elif abs(gap_pct) >= 0.5:
                reasons.append(f"Gap {gap_pct:+.1f}%")
            if abs(change_pct) >= 3:
                reasons.append(f"Move {change_pct:+.1f}%")
            if day_range_pct >= 3:
                reasons.append(f"Range {day_range_pct:.1f}%")

            candidates.append({
                "ticker": f"{sym}.NS",
                "name": name_map.get(sym, ""),
                "ltp": round(ltp, 2),
                "prev_close": round(prev_close, 2),
                "gap_pct": round(gap_pct, 2),
                "change_pct": round(change_pct, 2),
                "day_range_pct": round(day_range_pct, 2),
                "score": round(score, 2),
                "reason": " | ".join(reasons) if reasons else "Active",
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:max_candidates]
        logger.info(f"🎯 Phase 1: {len(candidates)} active → top {len(top)} candidates for LLM")
        return top

    # ── Phase 2: LLM Selection ────────────────────────────────

    def scan_phase2_llm(
        self,
        candidates: List[Dict],
        llm_client,
        llm_config,
        learnings: str = "",
        news: str = "",
        max_picks: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Phase 2: LLM reviews candidates and picks the best 25 for scalping.

        The LLM sees:
        - All ~60 candidates with gap, range, price
        - Today's market news/sentiment
        - Distilled rules from past trades
        - And returns its top picks with reasoning
        """
        if not candidates:
            return []

        # Build candidate table for LLM
        table_lines = []
        for i, c in enumerate(candidates):
            name = (c.get("name", "") or "")[:20]
            table_lines.append(
                f"{i+1:2d}. {c['ticker']:15s} {name:20s} Rs{c['ltp']:>8.1f} "
                f"gap={c['gap_pct']:+5.1f}% chg={c['change_pct']:+5.1f}% range={c['day_range_pct']:4.1f}% "
                f"— {c['reason']}"
            )
        candidates_text = "\n".join(table_lines)

        prompt = f"""You are an expert intraday stock picker for NSE India.

## Today's Pre-Market Candidates ({len(candidates)} stocks with significant moves)
{candidates_text}

## Market Context
{news[:2000] if news else "No news available yet."}

## Your Past Learnings (what worked, what didn't)
{learnings[:3000] if learnings else "No learnings yet — this is the first scan."}

---

## Task
From the {len(candidates)} candidates above, pick the BEST {max_picks} stocks for intraday scalping today.

For each pick, briefly explain WHY (1 line):
- Is it a gap-up momentum play? Gap-down reversal? Range breakout?
- Does your past experience (learnings) suggest this type of setup works?
- Is the price range good for scalping? (₹100-2000 ideal)

## Rules
- Pick EXACTLY {max_picks} stocks (no more, no less)
- Include BOTH long candidates (gap-up + momentum) AND short candidates (gap-down + overbought)
- Prefer liquid large/mid-caps over illiquid small-caps
- Avoid stocks you've lost money on repeatedly (check learnings)
- Each pick should have a clear scalping thesis

## Output Format
Return a JSON array of objects, each with:
- "ticker": the ticker symbol (e.g. "RELIANCE.NS")
- "direction": "long" or "short" (your preferred trade direction)
- "reason": brief 1-line thesis

Example:
```json
[
  {{"ticker": "RELIANCE.NS", "direction": "long", "reason": "Gap-up +2.3% with sector momentum, good for breakout scalp"}},
  {{"ticker": "BAJFINANCE.NS", "direction": "short", "reason": "Gap-down -1.5%, likely to test lower support, short scalp"}}
]
```

Return ONLY the JSON array, no other text."""

        try:
            logger.info(f"🧠 Phase 2: LLM reviewing {len(candidates)} candidates...")

            if llm_config.llm_provider == "anthropic":
                response = llm_client.messages.create(
                    model=llm_config.anthropic_model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()
            else:
                model = (
                    llm_config.openrouter_model
                    if llm_config.llm_provider == "openrouter"
                    else llm_config.openai_model
                )
                response = llm_client.chat.completions.create(
                    model=model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.choices[0].message.content.strip()

            # Parse JSON from response
            import re
            json_match = re.search(r'\[[\s\S]*\]', text)
            if not json_match:
                logger.warning("LLM didn't return valid JSON — using Phase 1 ranking")
                return candidates[:max_picks]

            picks = json.loads(json_match.group())

            # Enrich picks with data from candidates
            candidate_map = {c["ticker"]: c for c in candidates}
            enriched = []
            for pick in picks[:max_picks]:
                ticker = pick.get("ticker", "")
                if ticker in candidate_map:
                    entry = candidate_map[ticker].copy()
                    entry["llm_direction"] = pick.get("direction", "long")
                    entry["llm_reason"] = pick.get("reason", "")
                    enriched.append(entry)

            logger.info(f"🎯 Phase 2: LLM picked {len(enriched)} stocks")
            for i, s in enumerate(enriched[:10]):
                dir_icon = "📈" if s.get("llm_direction") == "long" else "📉"
                logger.info(f"  {i+1:2d}. {dir_icon} {s['ticker']:15s} — {s.get('llm_reason', '')[:60]}")

            return enriched if enriched else candidates[:max_picks]

        except Exception as e:
            logger.error(f"Phase 2 LLM selection failed: {e}")
            return candidates[:max_picks]

    # ── Phase 1 Historical: For Backtesting ─────────────────────

    def scan_phase1_historical(
        self,
        daily_candles: Dict[str, "pd.DataFrame"],
        max_candidates: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Phase 1 for backtesting: same filtering logic as scan_phase1,
        but uses pre-fetched daily candle data instead of live kite.ohlc().

        daily_candles: {ticker: DataFrame[date, open, high, low, close, volume]}
                       Must have at least 2 rows (prev day + current day).
        """
        import pandas as pd

        # Load instrument cache for company names
        name_map = {}
        if INSTRUMENT_CACHE.exists():
            try:
                data = json.loads(INSTRUMENT_CACHE.read_text())
                for i in data.get("instruments", []):
                    sym = i.get("tradingsymbol", "")
                    name_map[f"{sym}.NS"] = i.get("name", "")
                    name_map[sym] = i.get("name", "")
            except Exception:
                pass

        candidates = []
        for ticker, df in daily_candles.items():
            if df is None or len(df) < 2:
                continue

            # Last row = trade day, second-to-last = previous close
            today = df.iloc[-1]
            prev = df.iloc[-2]

            ltp = float(today["close"])
            prev_close = float(prev["close"])
            day_open = float(today["open"])
            day_high = float(today["high"])
            day_low = float(today["low"])

            if not prev_close or not ltp or ltp < MIN_PRICE or ltp > MAX_PRICE:
                continue

            gap_pct = ((day_open - prev_close) / prev_close) * 100
            change_pct = ((ltp - prev_close) / prev_close) * 100
            day_range_pct = ((day_high - day_low) / prev_close) * 100 if day_high > day_low else 0

            # Same filter as live scan
            if abs(gap_pct) < MIN_GAP_PCT and abs(change_pct) < 1.0 and day_range_pct < 1.0:
                continue

            score = min(abs(gap_pct), 5) * 2 + min(abs(change_pct), 10) * 1 + min(day_range_pct, 8) * 0.5
            if 100 <= ltp <= 2000:
                score += 1.5

            reasons = []
            if abs(gap_pct) >= 2:
                reasons.append(f"{'Gap-up' if gap_pct > 0 else 'Gap-down'} {gap_pct:+.1f}%")
            elif abs(gap_pct) >= 0.5:
                reasons.append(f"Gap {gap_pct:+.1f}%")
            if abs(change_pct) >= 3:
                reasons.append(f"Move {change_pct:+.1f}%")
            if day_range_pct >= 3:
                reasons.append(f"Range {day_range_pct:.1f}%")

            candidates.append({
                "ticker": ticker if ".NS" in ticker else f"{ticker}.NS",
                "name": name_map.get(ticker, name_map.get(ticker.replace(".NS", ""), "")),
                "ltp": round(ltp, 2),
                "prev_close": round(prev_close, 2),
                "gap_pct": round(gap_pct, 2),
                "change_pct": round(change_pct, 2),
                "day_range_pct": round(day_range_pct, 2),
                "score": round(score, 2),
                "reason": " | ".join(reasons) if reasons else "Active",
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:max_candidates]
        logger.info(f"🎯 Historical Phase 1: {len(candidates)} active → top {len(top)} candidates")
        return top

    def scan_historical(
        self,
        daily_candles: Dict[str, "pd.DataFrame"],
        max_stocks: int = 25,
        llm_client=None,
        llm_config=None,
        learnings: str = "",
        news: str = "",
    ) -> List[Dict[str, Any]]:
        """Full historical scan: Phase 1 (quantitative) + Phase 2 (LLM)."""
        candidates = self.scan_phase1_historical(daily_candles, max_candidates=60)
        if not candidates:
            return []

        if llm_client and llm_config:
            return self.scan_phase2_llm(
                candidates, llm_client, llm_config,
                learnings=learnings, news=news,
                max_picks=max_stocks,
            )

        logger.info("⚠️ No LLM client for Phase 2 — using quantitative ranking only")
        return candidates[:max_stocks]

    # ── Combined Scan ─────────────────────────────────────────

    def scan(self, max_stocks: int = 30, llm_client=None, llm_config=None,
             learnings: str = "", news: str = "") -> List[Dict[str, Any]]:
        """
        Full two-phase scan: quantitative filter → LLM selection.
        Falls back to Phase 1 only if LLM is not available.
        """
        # Phase 1: Quantitative filter
        candidates = self.scan_phase1(max_candidates=60)

        if not candidates:
            return []

        # Phase 2: LLM selection (if client available)
        if llm_client and llm_config:
            return self.scan_phase2_llm(
                candidates, llm_client, llm_config,
                learnings=learnings, news=news,
                max_picks=max_stocks,
            )

        # Fallback: just use Phase 1 ranking
        logger.info("⚠️ No LLM client for Phase 2 — using quantitative ranking only")
        return candidates[:max_stocks]

    def get_watchlist_tickers(self, max_stocks: int = 30, **kwargs) -> List[str]:
        """Run scan and return just the ticker list."""
        return [r["ticker"] for r in self.scan(max_stocks, **kwargs)]

    def get_scan_summary(self, max_stocks: int = 30, **kwargs) -> str:
        """Run scan and return formatted summary for LLM context."""
        results = self.scan(max_stocks, **kwargs)
        if not results:
            return "Pre-market scan: No significant movers found."

        lines = [f"## Pre-Market Scan — Top {len(results)} Stocks Selected for Today"]
        lines.append(f"{'#':>2s} {'Ticker':15s} {'Name':20s} {'LTP':>8s} {'Gap%':>7s} {'Dir':>5s} Thesis")
        lines.append("-" * 90)
        for i, s in enumerate(results):
            name = (s.get("name", "") or "")[:20]
            direction = s.get("llm_direction", "?")
            reason = s.get("llm_reason", s.get("reason", ""))[:40]
            lines.append(
                f"{i+1:2d} {s['ticker']:15s} {name:20s} {s['ltp']:>8.1f} {s['gap_pct']:>+6.1f}% "
                f"{direction:>5s} {reason}"
            )
        return "\n".join(lines)
