"""
Backtest Engine — Replays historical trading days to pre-train agents.

For each simulated day:
1. Fetch Day N-1 closing data → run pre-market scanner
2. Fetch Day N 15-min candles for selected stocks
3. Step through each candle bar → call agent.run_once()
4. Force-close positions at market close
5. Run daily review → update distilled rules

After N days: agent has a full set of trades + distilled rules,
ready to go live with proven strategies.
"""

import json
import logging
import time as _time
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

import pandas as pd

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Orchestrates multi-day backtesting of a trading session."""

    def __init__(self, session_config, kite_client, llm_config=None):
        self.session_config = session_config
        self.kite = kite_client
        self.llm_config = llm_config
        self._instrument_tokens: Dict[str, int] = {}
        self._load_instrument_tokens()

    def _load_instrument_tokens(self):
        """Cache instrument tokens for historical data lookups."""
        try:
            instruments = self.kite.instruments("NSE")
            for i in instruments:
                sym = i.get("tradingsymbol", "")
                self._instrument_tokens[sym] = i.get("instrument_token")
                self._instrument_tokens[f"{sym}.NS"] = i.get("instrument_token")
        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")

    def _get_token(self, ticker: str) -> Optional[int]:
        """Get instrument token for a ticker."""
        return self._instrument_tokens.get(ticker) or self._instrument_tokens.get(ticker.replace(".NS", ""))

    def _get_trading_days(self, start: date, end: date) -> List[date]:
        """Return list of weekdays between start and end (excl weekends)."""
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # Mon-Fri
                days.append(current)
            current += timedelta(days=1)
        return days

    def _fetch_daily_candles(self, tickers: List[str], end_date: date, lookback_days: int = 35) -> Dict[str, pd.DataFrame]:
        """Fetch daily OHLCV candles for multiple tickers."""
        start = (end_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end = end_date.strftime("%Y-%m-%d")
        result = {}

        for ticker in tickers:
            token = self._get_token(ticker)
            if not token:
                continue
            try:
                candles = self.kite.historical_data(
                    instrument_token=token,
                    from_date=start,
                    to_date=end,
                    interval="day",
                )
                if candles:
                    result[ticker] = pd.DataFrame(candles)
            except Exception as e:
                logger.debug(f"Daily candles failed for {ticker}: {e}")
            _time.sleep(0.1)  # Gentle rate limiting

        return result

    def _fetch_intraday_candles(self, tickers: List[str], trade_date: date, interval: str = "15minute") -> Dict[str, pd.DataFrame]:
        """Fetch intraday candles for multiple tickers on a specific date."""
        date_str = trade_date.strftime("%Y-%m-%d")
        result = {}

        for ticker in tickers:
            token = self._get_token(ticker)
            if not token:
                continue
            try:
                candles = self.kite.historical_data(
                    instrument_token=token,
                    from_date=date_str,
                    to_date=date_str,
                    interval=interval,
                )
                if candles:
                    result[ticker] = pd.DataFrame(candles)
            except Exception as e:
                logger.debug(f"Intraday candles failed for {ticker}: {e}")
            _time.sleep(0.1)

        return result

    def _fetch_daily_candles_fast(self, tickers: List[str], end_date: date, lookback_days: int = 5, on_progress=None) -> Dict[str, pd.DataFrame]:
        """Fast bulk fetch of short-lookback daily candles for scanner.
        Uses minimal sleep (0.05s) and skips failures silently.
        Good for scanning 2000+ stocks where we just need recent OHLC.
        on_progress: optional callback(scanned, total) called every ~50 tickers
        for live UI updates."""
        start = (end_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end = end_date.strftime("%Y-%m-%d")
        result = {}
        failed = 0

        for idx, ticker in enumerate(tickers):
            token = self._get_token(ticker)
            if not token:
                continue
            try:
                candles = self.kite.historical_data(
                    instrument_token=token,
                    from_date=start,
                    to_date=end,
                    interval="day",
                )
                if candles and len(candles) >= 2:
                    result[ticker] = pd.DataFrame(candles)
            except Exception:
                failed += 1
            # Live progress callback every 50 tickers
            if on_progress and (idx + 1) % 50 == 0:
                try:
                    on_progress(idx + 1, len(tickers))
                except Exception:
                    pass
            # Light rate limiting — 0.05s per ticker
            if (idx + 1) % 100 == 0:
                logger.info(f"    Scanner fetch: {idx + 1}/{len(tickers)} done ({len(result)} ok, {failed} failed)")
                _time.sleep(0.5)  # Brief pause every 100 tickers
            else:
                _time.sleep(0.05)

        logger.info(f"  📊 Scanner fetch complete: {len(result)}/{len(tickers)} stocks with data")
        return result

    def _get_previous_day_ohlc(self, tickers: List[str], trade_date: date) -> Dict[str, Dict]:
        """Get previous trading day's OHLC for pre-market scanner input."""
        prev_date = trade_date - timedelta(days=1)
        # Skip weekends
        while prev_date.weekday() >= 5:
            prev_date -= timedelta(days=1)

        daily = self._fetch_daily_candles(tickers, prev_date, lookback_days=5)
        result = {}
        for ticker, df in daily.items():
            if df.empty:
                continue
            last = df.iloc[-1]
            result[ticker] = {
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "close": float(last["close"]),
                "volume": int(last.get("volume", 0)),
            }
        return result

    def run(
        self,
        start_date: date,
        end_date: date,
        watchlist: List[str] = None,
        interval: str = "15minute",
        progress_callback: Callable = None,
    ) -> Dict[str, Any]:
        """
        Main backtest loop. Simulates trading across multiple days.

        Args:
            start_date: First trading day to simulate
            end_date: Last trading day to simulate
            watchlist: Stocks to trade (if None, uses session default)
            interval: Candle interval ("15minute", "5minute")
            progress_callback: Called with status dict after each day

        Returns:
            Summary dict with total trades, P&L, win rate, etc.
        """
        from .backtest_data import BacktestMarketData
        from .runner import TradingAgent
        from .session import load_session

        trading_days = self._get_trading_days(start_date, end_date)
        if not trading_days:
            return {"error": "No trading days in range"}

        logger.info(f"🔄 Starting backtest: {len(trading_days)} days ({start_date} to {end_date})")

        # Initialize the agent with the session config
        session = load_session(self.session_config.session_id)
        agent = TradingAgent(session=session)
        # Disable all live web searches during backtest (would leak future news)
        agent._is_backtest = True
        if hasattr(agent, "researcher"):
            agent.researcher.backtest_mode = True
        preset = agent.preset
        base_tickers = watchlist or agent.config.watchlist

        # Make portfolio use simulated time for entry_time/exit_time so
        # hold durations are meaningful (real wall clock is meaningless
        # since the engine processes a full trading day in seconds)
        def _sim_clock():
            t = getattr(agent.market_data, "current_time", None)
            return t if t is not None else datetime.now()
        agent.portfolio._clock = _sim_clock

        # Pre-market scanner for dynamic stock selection each day
        from .premarket_scanner import PreMarketScanner
        scanner = PreMarketScanner(self.kite)
        # Build scan pool from cached instrument list (all ~3000 NSE equities)
        scan_instruments = scanner._ensure_instrument_cache()
        scan_pool = [f"{i['tradingsymbol']}.NS" for i in scan_instruments]
        if len(scan_pool) < 100:
            # Fallback to base watchlist if instrument cache is empty
            scan_pool = list(base_tickers)
        logger.info(f"📋 Scanner pool: {len(scan_pool)} stocks (will pick top 25 each day)")

        results = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "trading_days": len(trading_days),
            "daily_results": [],
            "status": "running",
            "current_day": 0,
            "current_date": trading_days[0].isoformat() if trading_days else None,
            "current_phase": "init",
            "current_bar_time": None,
            "current_picks": [],
            "recent_trades": [],
            "day_trades_count": 0,
            "day_pnl": 0.0,
            "day_started_at": None,
            "started_at": _time.time(),
            # Sub-progress within a phase (e.g. scanning 2200/2847)
            "phase_progress": None,
            "phase_total": None,
            "phase_detail": None,
        }

        # Save progress file
        progress_path = Path(f"sessions/{self.session_config.session_id}/backtest_progress.json")
        progress_path.parent.mkdir(parents=True, exist_ok=True)

        def _save():
            try:
                progress_path.write_text(json.dumps(results, indent=2, default=str))
            except Exception as _e:
                logger.warning(f"  Progress write failed: {_e}")

        def _snapshot_recent_trades():
            """Pull last 8 closed trades for the live feed."""
            try:
                closed = agent.portfolio.get_closed_trades(limit=8)
                return [{
                    "ticker": t.ticker,
                    "action": t.action,
                    "direction": t.direction,
                    "quantity": t.quantity,
                    "entry_price": round(t.entry_price, 2),
                    "exit_price": round(t.exit_price, 2) if t.exit_price else None,
                    "pnl": round(t.pnl, 2) if t.pnl is not None else None,
                    "exit_time": t.exit_time,
                } for t in closed]
            except Exception:
                return []

        # Write initial progress immediately so UI can show "running" from start
        _save()

        for day_idx, trade_date in enumerate(trading_days):
            # Snapshot total_value at start of day for accurate daily P&L
            try:
                day_start_value = agent.portfolio.get_portfolio_summary({}).get("total_value", 0)
            except Exception:
                day_start_value = 0
            # Reset day-scoped fields
            results["current_day"] = day_idx + 1
            results["current_date"] = trade_date.isoformat()
            results["current_phase"] = "scanning"
            results["current_bar_time"] = None
            results["current_picks"] = []
            results["day_trades_count"] = 0
            results["day_pnl"] = 0.0
            results["day_started_at"] = _time.time()
            results["phase_progress"] = 0
            results["phase_total"] = len(scan_pool)
            results["phase_detail"] = None
            _save()
            day_start = _time.time()

            # Scanner progress callback — updates UI every 50 stocks
            def _on_scan_progress(scanned, total):
                results["phase_progress"] = scanned
                results["phase_total"] = total
                _save()
            logger.info(f"\n{'='*60}")
            logger.info(f"📅 Backtest Day {day_idx + 1}/{len(trading_days)}: {trade_date}")
            logger.info(f"{'='*60}")

            try:
                # 0. Run historical pre-market scanner to pick today's stocks
                #    Scans all ~3000 NSE equities — fast fetch with 2-day lookback
                logger.info(f"  🔍 Running pre-market scanner ({len(scan_pool)} stocks) for {trade_date}...")
                broad_daily = self._fetch_daily_candles_fast(scan_pool, trade_date, lookback_days=5, on_progress=_on_scan_progress)

                if broad_daily:
                    # Phase: LLM picking top 25 stocks
                    results["current_phase"] = "selecting"
                    results["phase_progress"] = None
                    results["phase_total"] = None
                    results["phase_detail"] = f"{len(broad_daily)} candidates passed Phase 1"
                    _save()

                    # Get agent's learnings for LLM Phase 2
                    learnings = agent.learner.get_learnings(max_chars=3000)
                    scan_results = scanner.scan_historical(
                        daily_candles=broad_daily,
                        max_stocks=25,
                        llm_client=agent.engine.client if agent.engine else None,
                        llm_config=agent.config if agent.engine else None,
                        learnings=learnings,
                    )
                    if scan_results:
                        tickers = [s["ticker"] for s in scan_results]
                        # Save picks for UI display
                        results["current_picks"] = [{
                            "ticker": s["ticker"],
                            "direction": s.get("llm_direction", s.get("direction", "long")),
                            "reason": (s.get("llm_reason") or s.get("reason") or "")[:120],
                        } for s in scan_results]
                        _save()

                        # Build scan summary for agent context
                        scan_lines = [f"📊 Pre-market scan picked {len(tickers)} stocks:"]
                        for s in scan_results[:10]:
                            d = s.get("llm_direction", "?")
                            scan_lines.append(f"  {'📈' if d == 'long' else '📉'} {s['ticker']} ({s.get('llm_reason', s.get('reason', ''))[:50]})")
                        agent._premarket_summary = "\n".join(scan_lines)
                        logger.info(f"  🎯 Scanner selected {len(tickers)} stocks for today")
                    else:
                        tickers = base_tickers
                        results["current_picks"] = [{"ticker": t, "direction": "long", "reason": "default watchlist"} for t in tickers[:25]]
                        _save()
                        logger.info(f"  ⚠️ Scanner returned no picks — using default {len(tickers)} watchlist")
                else:
                    tickers = base_tickers
                    results["current_picks"] = [{"ticker": t, "direction": "long", "reason": "default watchlist"} for t in tickers[:25]]
                    _save()
                    logger.info(f"  ⚠️ No broad daily data — using default {len(tickers)} watchlist")

                # Update agent's watchlist and config for this day
                agent.config.watchlist = list(tickers)
                agent.market_data.watchlist = list(tickers)

                # 1. Fetch daily candles for technical indicators (35-day lookback)
                #    Only for the selected tickers (not all instruments)
                logger.info(f"  📊 Fetching daily candles for {len(tickers)} stocks...")
                daily_candles = self._fetch_daily_candles(tickers, trade_date, lookback_days=35)

                # 2. Fetch intraday candles for this trading day
                logger.info(f"  📊 Fetching {interval} intraday candles...")
                intraday_candles = self._fetch_intraday_candles(tickers, trade_date, interval)

                if not intraday_candles:
                    logger.warning(f"  No intraday data for {trade_date} — skipping (holiday?)")
                    continue

                # Log candle key → watchlist match
                candle_keys = set(intraday_candles.keys())
                watchlist_set = set(tickers)
                matched = candle_keys & watchlist_set
                unmatched = watchlist_set - candle_keys
                logger.info(f"  📋 Candle keys matched: {len(matched)}/{len(tickers)} "
                           f"| unmatched: {list(unmatched)[:5] if unmatched else 'none'}")

                # 3. Create BacktestMarketData
                backtest_data = BacktestMarketData(
                    watchlist=tickers,
                    market_preset=preset,
                    intraday_candles=intraday_candles,
                    daily_candles=daily_candles,
                )

                # Replace the agent's market data with backtest data
                agent.market_data = backtest_data

                # 4. Get all timestamps to step through
                # Collect all unique candle timestamps from all tickers
                all_times = set()
                for df in intraday_candles.values():
                    if not df.empty:
                        for t in df["date"]:
                            if hasattr(t, 'timestamp'):
                                all_times.add(t.replace(tzinfo=None) if t.tzinfo else t)
                            else:
                                all_times.add(pd.Timestamp(t).to_pydatetime().replace(tzinfo=None))

                timestamps = sorted(all_times)
                if not timestamps:
                    logger.warning(f"  No candle timestamps for {trade_date}")
                    continue

                logger.info(f"  ⏱ Stepping through {len(timestamps)} candles...")

                # Phase: actively trading
                results["current_phase"] = "trading"
                results["phase_progress"] = 0
                results["phase_total"] = len(timestamps)
                results["phase_detail"] = None
                _save()

                # 5. Step through each candle
                day_trades = 0
                for ts_idx, ts in enumerate(timestamps):
                    backtest_data.set_time(ts)

                    # Skip if outside market hours
                    if not backtest_data.is_market_open():
                        continue

                    # Update bar time so UI shows current trading bar
                    results["current_bar_time"] = ts.strftime("%H:%M")
                    results["phase_progress"] = ts_idx + 1

                    # Run one agent cycle
                    try:
                        result = agent.run_once(force_intraday=True, is_backtest=True)
                        # Count successful ENTRIES only (BUY + SHORT). Each action is
                        # {"tool": "place_trade", "input": {...}, "result": {action, success, ...}}
                        cycle_actions = result.get("actions", []) or []
                        def _is_entry(a):
                            r = a.get("result") or {}
                            return r.get("success") and r.get("action") in ("BUY", "SHORT")
                        entries = sum(1 for a in cycle_actions if _is_entry(a))
                        day_trades += entries
                        if cycle_actions:
                            logger.info(f"    {ts.strftime('%H:%M')} — {len(cycle_actions)} action(s), {entries} new entr{'y' if entries == 1 else 'ies'}")
                    except Exception as e:
                        logger.warning(f"    {ts.strftime('%H:%M')} — cycle error: {e}")

                    # Update progress on every bar so UI shows live activity
                    results["day_trades_count"] = day_trades
                    # Refresh recent trades feed every bar (cheap query)
                    results["recent_trades"] = _snapshot_recent_trades()
                    # Day P&L: end-of-bar total_value minus start-of-day total_value
                    try:
                        portfolio_now = agent.portfolio.get_portfolio_summary(backtest_data.get_current_prices())
                        results["day_pnl"] = round(portfolio_now.get("total_value", 0) - day_start_value, 2)
                    except Exception:
                        pass
                    _save()

                # Phase: closing intraday positions
                open_positions_count = len(agent.portfolio.get_open_positions())
                results["current_phase"] = "closing"
                results["current_bar_time"] = None
                results["phase_progress"] = None
                results["phase_total"] = None
                results["phase_detail"] = f"{open_positions_count} position(s) to close" if open_positions_count else None
                _save()

                # 6. Force-close all positions at end of day
                prices = backtest_data.get_current_prices()
                positions = agent.portfolio.get_open_positions()
                for pos in positions:
                    price = prices.get(pos.ticker, pos.entry_price)
                    try:
                        if pos.direction == "short":
                            agent.portfolio.execute_cover(pos.id, price, reason="Backtest EOD close", exit_type="forced_close")
                        else:
                            agent.portfolio.execute_sell(pos.id, price, reason="Backtest EOD close", exit_type="forced_close")
                    except Exception:
                        pass

                # Phase: reviewing & learning
                results["current_phase"] = "reviewing"
                results["phase_progress"] = None
                results["phase_total"] = None
                results["phase_detail"] = "Updating distilled rules from today's trades"
                results["recent_trades"] = _snapshot_recent_trades()
                _save()

                # 7. Run daily review to update distilled rules
                try:
                    agent.run_daily_review()
                except Exception as e:
                    logger.warning(f"  Daily review failed: {e}")

                # 8. Day summary
                portfolio = agent.portfolio.get_portfolio_summary(prices)
                stats = agent.learner.get_performance_stats()
                day_duration = _time.time() - day_start

                day_total_value = portfolio.get("total_value", 0)
                # Robust daily P&L = end-of-day total_value - start-of-day total_value
                # (works even if entry_time filter has edge cases)
                day_pnl_computed = round(day_total_value - day_start_value, 2)

                day_result = {
                    "date": trade_date.isoformat(),
                    "trades": day_trades,
                    "total_value": round(day_total_value, 2),
                    "daily_pnl": day_pnl_computed,
                    "total_return_pct": round(portfolio.get("total_return_pct", 0), 2),
                    "win_rate": stats.get("win_rate", 0),
                    "total_trades": stats.get("total_trades", 0),
                    "duration_sec": round(day_duration, 1),
                }
                results["daily_results"].append(day_result)

                logger.info(f"  ✅ Day {day_idx + 1} complete: {day_trades} entries, "
                           f"P&L: {day_pnl_computed:+.2f}, "
                           f"Total: {portfolio.get('total_return_pct', 0):+.2f}% "
                           f"({day_duration:.1f}s)")

                # Phase: day done
                results["current_phase"] = "day_done"
                results["current_day"] = day_idx + 1
                results["current_date"] = trade_date.isoformat()
                results["day_trades_count"] = day_trades
                results["day_pnl"] = day_pnl_computed
                results["recent_trades"] = _snapshot_recent_trades()
                _save()

                # Callback
                if progress_callback:
                    progress_callback(results)

            except Exception as e:
                logger.error(f"  ❌ Day {trade_date} failed: {e}")
                results["daily_results"].append({
                    "date": trade_date.isoformat(),
                    "error": str(e),
                })

        # Final stats
        results["status"] = "completed"
        final_stats = agent.learner.get_performance_stats()
        results["final_stats"] = final_stats
        progress_path.write_text(json.dumps(results, indent=2))

        logger.info(f"\n🏁 Backtest complete: {final_stats.get('total_trades', 0)} trades, "
                   f"WR: {final_stats.get('win_rate', 0)}%, "
                   f"P&L: {final_stats.get('total_pnl', 0):+.2f}")

        return results
