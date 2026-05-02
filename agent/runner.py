"""
Agent Runner — the main orchestrator that ties everything together.
Runs the Observe → Decide → Act → Learn loop on a schedule.
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, Any
from pathlib import Path

from .config import AgentConfig
from .portfolio import Portfolio
from .market_data import MarketData, create_market_data
from .web_research import WebResearcher
from .decision_engine import create_engine
from .risk_manager import RiskManager
from .learner import Learner

logger = logging.getLogger(__name__)


class TradingAgent:
    """
    The autonomous trading agent.
    Call `run_once()` for a single decision cycle, or `run_loop()` for continuous operation.

    Can be initialized with:
      - config: AgentConfig  (legacy, backward compat)
      - session: SessionConfig  (new multi-session mode)
    """

    def __init__(self, config: AgentConfig = None, session=None):
        # Session-based init
        if session is not None:
            from .market_presets import get_preset
            self.session = session
            self.preset = get_preset(session.market)
            self.config = AgentConfig.from_session(session)
        else:
            self.session = None
            self.preset = getattr(config, '_market_preset', None) if config else None
            self.config = config or AgentConfig()

        sym = self.config.currency_symbol

        # Ensure directories exist
        for path in [self.config.db_path, self.config.learnings_path, self.config.log_path]:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Setup logging — force=True clears any handlers added by a previous
        # instance that briefly shared this process space, preventing double-logs
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(self.config.log_path),
                logging.StreamHandler(),
            ],
            force=True,
        )

        # Initialize components — pass market_preset for market-aware behaviour
        self.portfolio = Portfolio(self.config.db_path, self.config.starting_capital)
        self.market_data = create_market_data(self.config, market_preset=self.preset)
        self.researcher = WebResearcher(self.config.news_sources, market_preset=self.preset)
        self.risk_manager = RiskManager(self.config, self.portfolio)
        self.learner = Learner(self.config, self.portfolio)

        # Engine init requires API key — defer failure to when engine is actually used
        # so that --status and other read-only commands work without a key
        try:
            self.engine = create_engine(self.config)
        except (ValueError, ImportError) as e:
            logger.warning(f"⚠️  LLM engine not available: {e}")
            self.engine = None

        self._is_backtest = False  # Set True by backtest engine to skip real-time checks

        market_label = self.preset.display_name if self.preset else "NSE"
        session_label = f" (session: {self.session.session_id})" if self.session else ""
        logger.info(f"🤖 Trading Agent initialized{session_label}")
        logger.info(f"   Market: {market_label}")
        logger.info(f"   Capital: {sym}{self.config.starting_capital:,.0f}")
        logger.info(f"   Watchlist: {len(self.config.watchlist)} assets")
        active_model = {
            "anthropic": self.config.anthropic_model,
            "openai": self.config.openai_model,
            "openrouter": self.config.openrouter_model,
        }.get(self.config.llm_provider, "unknown")
        logger.info(f"   LLM: {self.config.llm_provider} / {active_model}")

    # ── Tool Handler ─────────────────────────────────────────

    def handle_tool_call(self, tool_name: str, tool_input: Dict) -> Any:
        """
        Handle tool calls from the LLM.
        This is the bridge between the LLM's decisions and the real systems.
        """
        logger.info(f"🔧 Tool call: {tool_name}({json.dumps(tool_input)[:200]})")

        if tool_name == "search_market_news":
            query = tool_input.get("query", "")
            # During backtest, NEVER fetch live news — it would leak future
            # information into past decisions, and yfinance spam pollutes logs
            if getattr(self, "_is_backtest", False):
                return {"results": [], "note": "News disabled in backtest mode (would leak future info)"}
            # Execute the LLM's actual query via DuckDuckGo
            results = self.researcher.search(query, max_results=6)
            # If DDG returned nothing, fall back to yfinance news for any
            # tickers mentioned in the query
            if not results:
                query_upper = query.upper()
                matched_tickers = [
                    t for t in self.config.watchlist
                    if t.replace(".NS", "").replace(".BO", "") in query_upper
                ] or self.config.watchlist[:3]
                for ticker in matched_tickers[:3]:
                    results.extend(self.researcher.get_news_for_stock(ticker))
            # Log all results so user can see exactly what the LLM received
            if results:
                logger.info(f"📰 Search '{query[:60]}' → {len(results)} results:")
                for i, r in enumerate(results, 1):
                    src = r.get('source', '')
                    title = r.get('title', '')[:70]
                    snippet = r.get('snippet', '')[:120].replace('\n', ' ')
                    logger.info(f"   {i}. [{src}] {title}")
                    if snippet:
                        logger.info(f"      ↳ {snippet}")
            else:
                logger.info(f"📰 Search returned no results for: {query[:60]}")
            return {"query": query, "results": results[:8]}

        elif tool_name == "get_portfolio_status":
            prices = self.market_data.get_current_prices()
            return self.portfolio.get_portfolio_summary(prices)

        elif tool_name == "get_stock_details":
            ticker = tool_input.get("ticker", "")
            return self.market_data.get_stock_context(ticker)

        elif tool_name == "place_trade":
            return self._execute_trade(tool_input)

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    @property
    def _active_model(self) -> str:
        """Get the currently active LLM model name."""
        provider = self.config.llm_provider
        return getattr(self.config, f"{provider}_model", "unknown")

    def _execute_trade(self, trade_input: Dict) -> Dict:
        """Execute a trade with risk checks."""
        action = trade_input.get("action", "")
        reason = trade_input.get("reason", "")

        # Hard guard: block new positions near market close (force-close window)
        # Skip during backtest — backtest engine manages EOD close separately
        if action in ("BUY", "SHORT") and not self._is_backtest and self.risk_manager.check_intraday_close():
            msg = "Cannot open new positions — market closing soon"
            logger.warning(f"❌ {msg}")
            return {"success": False, "error": msg}

        if action == "BUY":
            ticker = trade_input.get("ticker", "")
            quantity = trade_input.get("quantity", 0)
            trade_type = trade_input.get("trade_type", "swing")

            # Get current price
            prices = self.market_data.get_current_prices([ticker])
            price = prices.get(ticker)
            if not price:
                return {"success": False, "error": f"Cannot get price for {ticker}"}

            # Risk check
            approved, risk_msg = self.risk_manager.check_buy(
                ticker, quantity, price, prices
            )
            if not approved:
                logger.warning(f"❌ Trade rejected: {risk_msg}")
                return {"success": False, "error": risk_msg}

            # Compute ATR-based stop and target (1.5×ATR stop, 2×ATR target → 1:2 RR)
            stop_price = target_price = atr_14 = None
            try:
                ctx = self.market_data.get_stock_context(ticker, include_vwap=False)
                atr_14 = ctx.get("atr_14")
                if atr_14 and atr_14 > 0:
                    stop_price   = round(price - 1.5 * atr_14, 2)
                    target_price = round(price + 2.0 * atr_14, 2)
                    sym = self.config.currency_symbol
                    logger.info(
                        f"📐 {ticker} ATR={atr_14:.2f} → "
                        f"stop {sym}{stop_price} / target {sym}{target_price}"
                    )
                else:
                    # Fallback: use config % if ATR unavailable
                    fallback_pct = self.config.per_trade_loss_limit_pct
                    stop_price   = round(price * (1 - fallback_pct), 2)
                    target_price = round(price * (1 + 2 * fallback_pct), 2)
                    logger.warning(f"ATR unavailable for {ticker}, using config % stop/target")
            except Exception as e:
                logger.warning(f"Stop/target calc failed for {ticker}: {e}")

            # Execute
            conviction = trade_input.get("conviction")
            trade = self.portfolio.execute_buy(
                ticker, quantity, price, trade_type, reason,
                stop_price=stop_price, target_price=target_price,
                conviction=conviction, llm_model=self._active_model,
            )
            self.learner.write_trade_log(trade)
            return {
                "success":      True,
                "trade_id":     trade.id,
                "action":       "BUY",
                "ticker":       ticker,
                "quantity":     quantity,
                "price":        price,
                "total_cost":   round(quantity * price, 2),
                "stop_price":   stop_price,
                "target_price": target_price,
                "atr_14":       atr_14,
            }

        elif action == "SELL":
            trade_id = trade_input.get("trade_id")
            if not trade_id:
                return {"success": False, "error": "trade_id required for SELL"}

            # Risk check
            approved, risk_msg = self.risk_manager.check_sell(trade_id)
            if not approved:
                return {"success": False, "error": risk_msg}

            # Get current price for the position
            positions = self.portfolio.get_open_positions()
            pos = next((p for p in positions if p.id == trade_id), None)
            if not pos:
                return {"success": False, "error": f"No open position with id {trade_id}"}

            prices = self.market_data.get_current_prices([pos.ticker])
            price = prices.get(pos.ticker, pos.entry_price)

            trade = self.portfolio.execute_sell(trade_id, price, reason)
            self.learner.write_trade_log(trade, llm_client=self.engine.client)
            return {
                "success": True,
                "trade_id": trade_id,
                "action": "SELL",
                "ticker": trade.ticker,
                "exit_price": price,
                "pnl": round(trade.pnl, 2),
            }

        elif action == "SHORT":
            ticker = trade_input.get("ticker", "")
            quantity = trade_input.get("quantity", 0)

            if not ticker or not quantity:
                return {"success": False, "error": "ticker and quantity required for SHORT"}

            # Get current price
            prices = self.market_data.get_current_prices([ticker])
            price = prices.get(ticker)
            if not price:
                return {"success": False, "error": f"Cannot get price for {ticker}"}

            # Risk check (reuse buy limits — same capital at risk)
            approved, risk_msg = self.risk_manager.check_buy(
                ticker, quantity, price, prices
            )
            if not approved:
                logger.warning(f"❌ Short rejected: {risk_msg}")
                return {"success": False, "error": risk_msg}

            # For shorts: stop ABOVE entry, target BELOW entry
            stop_price = target_price = atr_14 = None
            try:
                ctx = self.market_data.get_stock_context(ticker, include_vwap=False)
                atr_14 = ctx.get("atr_14")
                if atr_14 and atr_14 > 0:
                    stop_price   = round(price + 1.5 * atr_14, 2)   # stop: price rises against us
                    target_price = round(price - 2.0 * atr_14, 2)   # target: price drops in our favour
                    sym = self.config.currency_symbol
                    logger.info(
                        f"📐 SHORT {ticker} ATR={atr_14:.2f} → "
                        f"stop {sym}{stop_price} / target {sym}{target_price}"
                    )
                else:
                    fallback_pct = self.config.per_trade_loss_limit_pct
                    stop_price   = round(price * (1 + fallback_pct), 2)
                    target_price = round(price * (1 - 2 * fallback_pct), 2)
                    logger.warning(f"ATR unavailable for {ticker}, using config % stop/target (short)")
            except Exception as e:
                logger.warning(f"Stop/target calc failed for SHORT {ticker}: {e}")

            conviction = trade_input.get("conviction")
            trade = self.portfolio.execute_short(
                ticker, quantity, price, reason,
                stop_price=stop_price, target_price=target_price,
                conviction=conviction, llm_model=self._active_model,
            )
            self.learner.write_trade_log(trade)
            return {
                "success":      True,
                "trade_id":     trade.id,
                "action":       "SHORT",
                "ticker":       ticker,
                "quantity":     quantity,
                "price":        price,
                "stop_price":   stop_price,
                "target_price": target_price,
                "atr_14":       atr_14,
            }

        elif action == "COVER":
            trade_id = trade_input.get("trade_id")
            if not trade_id:
                return {"success": False, "error": "trade_id required for COVER"}

            # Verify the short trade exists
            positions = self.portfolio.get_open_positions()
            pos = next((p for p in positions if p.id == trade_id and p.direction == "short"), None)
            if not pos:
                return {"success": False, "error": f"No open SHORT position with id {trade_id}"}

            prices = self.market_data.get_current_prices([pos.ticker])
            price = prices.get(pos.ticker, pos.entry_price)

            trade = self.portfolio.execute_cover(trade_id, price, reason)
            self.learner.write_trade_log(trade, llm_client=self.engine.client)
            return {
                "success":    True,
                "trade_id":   trade_id,
                "action":     "COVER",
                "ticker":     trade.ticker,
                "exit_price": price,
                "pnl":        round(trade.pnl, 2),
            }

        return {"success": False, "error": f"Unknown action: {action}"}

    # ── Day Goal Context (for prompt) ────────────────────────

    def _build_day_goal_context(self) -> str:
        """Build a 'Today's Performance' section so LLM has urgency framing.
        Computes elapsed/remaining time in the trading session, today's P&L,
        progress toward a daily target, and status (BEHIND / ON PACE / AHEAD / TARGET HIT).
        """
        try:
            preset = self.preset
            sym = self.config.currency_symbol if self.config else "₹"
            starting_capital = self.config.starting_capital

            # Daily target — default 0.8% for intraday markets, 0.3% for swing/24x7
            is_intraday_market = preset and preset.market_id == "nse-intraday"
            target_pct = 0.008 if is_intraday_market else 0.003
            target_pnl = starting_capital * target_pct

            # Today's P&L from portfolio
            try:
                prices = self.market_data.get_current_prices()
            except Exception:
                prices = {}
            portfolio = self.portfolio.get_portfolio_summary(prices)
            today_pnl = portfolio.get("today_pnl", 0)
            today_pnl_pct = (today_pnl / starting_capital * 100) if starting_capital else 0

            # Trade count today
            try:
                today_trades = len(self.portfolio.get_today_trades())
            except Exception:
                today_trades = 0

            # Time within session
            try:
                # Use simulated time during backtest
                if getattr(self, "_is_backtest", False) and getattr(self.market_data, "current_time", None):
                    now = self.market_data.current_time
                else:
                    from zoneinfo import ZoneInfo
                    tz = preset.timezone if preset else "Asia/Kolkata"
                    now = datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
                cur_min = now.hour * 60 + now.minute
            except Exception:
                cur_min = None

            session_str = ""
            pct_through = None
            if cur_min is not None and preset and preset.market_open and preset.market_close:
                try:
                    o_h, o_m = map(int, preset.market_open.split(":"))
                    c_h, c_m = map(int, preset.market_close.split(":"))
                    open_min = o_h * 60 + o_m
                    close_min = c_h * 60 + c_m
                    total = max(1, close_min - open_min)
                    elapsed = max(0, min(total, cur_min - open_min))
                    remaining = max(0, close_min - cur_min)
                    pct_through = round(elapsed / total * 100)
                    session_str = (
                        f"- Time: {now.strftime('%H:%M')} · {elapsed} min into session · "
                        f"{remaining} min remaining ({pct_through}% through day)"
                    )
                except Exception:
                    pass

            # Status label
            target_progress_pct = (today_pnl / target_pnl * 100) if target_pnl else 0
            if today_pnl >= target_pnl:
                status = "🎯 TARGET HIT — consider locking in gains; raise stops to break-even"
            elif pct_through is not None and target_progress_pct < pct_through - 20:
                status = "⚠️ BEHIND PACE — need higher-conviction setups; don't over-trade to catch up"
            elif pct_through is not None and target_progress_pct > pct_through + 20:
                status = "✅ AHEAD OF PACE — protect profits; tighten stops on winners"
            else:
                status = "📊 ON PACE"

            target_str = f"{sym}{target_pnl:,.0f} ({target_pct*100:.1f}%)"
            pnl_str = f"{sym}{today_pnl:+,.0f} ({today_pnl_pct:+.2f}%)"

            return (
                f"## Today's Performance & Goals\n"
                f"{session_str}\n"
                f"- Daily P&L: **{pnl_str}**\n"
                f"- Daily target: {target_str} · {target_progress_pct:.0f}% complete\n"
                f"- Trades today: {today_trades}\n"
                f"- Status: {status}"
            )
        except Exception as e:
            logger.debug(f"Day goal context build failed: {e}")
            return ""

    # ── Main Loop ────────────────────────────────────────────

    def run_once(self, force_intraday: bool = False, is_backtest: bool = False) -> Dict:
        """Run a single decision cycle.
        force_intraday: hint the LLM to prefer intraday trades (useful for testing).
        is_backtest: skip real-time close check (backtest manages EOD separately).
        """
        logger.info("=" * 60)
        # Track backtest mode on instance so _execute_trade can check it
        if is_backtest:
            self._is_backtest = True
        # Suppress intraday hint for 24/7 markets
        if force_intraday and self.preset and self.preset.is_24x7:
            force_intraday = False
        logger.info("🔄 Starting decision cycle" + (" [FORCE INTRADAY]" if force_intraday else "") + (" [BACKTEST]" if is_backtest else ""))

        # 1. OBSERVE — gather market data
        logger.info("👁️  Observing market...")
        prices = self.market_data.get_current_prices()
        watchlist_data = self.market_data.get_watchlist_summary()
        news = self.researcher.build_research_context(self.config.watchlist[:5])

        # Log a brief news summary so it's visible in the dashboard log panel
        if news and news != "No market news available at this time.":
            # Extract numbered headline lines: "  1. [source] title"
            import re
            headlines = [
                re.sub(r"^\s*\d+\.\s*", "", line).strip()
                for line in news.splitlines()
                if re.match(r"^\s*\d+\.\s*\[", line)
            ][:3]
            if headlines:
                logger.info(f"📰 News: {' | '.join(headlines)}")
            else:
                logger.info(f"📰 News context loaded ({len(news)} chars)")

        # 2. Check risk status
        risk_status = self.risk_manager.get_risk_status(prices)
        if not risk_status["can_trade"]:
            logger.warning("⚠️  Daily loss limit hit — no trading today")
            return {"status": "blocked", "reason": "daily loss limit"}

        # 3. Run stop/target sweep — writes journal entry for each closed position
        stopped = self.risk_manager.run_stop_loss_sweep(prices)
        if stopped:
            logger.info(f"🛑 Stop/target triggered on {len(stopped)} positions")
            for trade in stopped:
                self.learner.write_trade_log(trade, llm_client=self.engine.client)

        # 4. Force-close intraday positions near market close (≥15:15 IST)
        #    Skip during backtest — the backtest engine handles EOD close separately
        if not is_backtest:
            near_close = self.risk_manager.check_intraday_close()
            if near_close:
                self._close_intraday_positions(prices)
                # Don't let LLM open new positions when we're in the closing window
                logger.info("⏰ Near market close — skipping new trade decisions")
                return {"status": "ok", "actions": [], "portfolio": self.portfolio.get_portfolio_summary(prices)}

        # 5. DECIDE + ACT — let the LLM make decisions
        logger.info("🧠 Running decision engine...")
        portfolio_summary = self.portfolio.get_portfolio_summary(prices)
        learnings = self.learner.get_learnings(max_chars=6000)

        # Load live directives from operator and prepend to learnings
        directives_text = self._load_directives()
        if directives_text:
            logger.info(f"📋 Live directives active ({directives_text.count(chr(10))} items)")
            learnings = directives_text + "\n\n" + learnings

        # Add pre-market scan summary to news context if available
        premarket_summary = getattr(self, '_premarket_summary', '')
        if premarket_summary:
            news = premarket_summary + "\n\n" + news

        # If we're in backtest mode, expose simulated time to the prompt
        bt_date, bt_time = None, None
        if getattr(self, "_is_backtest", False):
            bt_now = getattr(self.market_data, "current_time", None)
            if bt_now is not None:
                try:
                    bt_date = bt_now.strftime("%Y-%m-%d")
                    bt_time = bt_now.strftime("%H:%M")
                except Exception:
                    pass

        actions = self.engine.run_decision_loop(
            portfolio_summary=portfolio_summary,
            watchlist_data=watchlist_data,
            news_context=news,
            risk_status=risk_status,
            learnings=learnings,
            tools=[],  # Tool defs are handled inside the engine
            tool_handler=self.handle_tool_call,
            is_market_open=self.market_data.is_market_open(),
            force_intraday=force_intraday,
            is_backtest=getattr(self, "_is_backtest", False),
            backtest_date=bt_date,
            backtest_time=bt_time,
            day_goal_context=self._build_day_goal_context() if getattr(self, "_is_backtest", False) or force_intraday else None,
        )

        logger.info(f"✅ Cycle complete — {len(actions)} actions taken")
        self._cleanup_cycle_directives()
        self._write_thinking_log(actions, bt_date, bt_time)
        self._write_cost_log(bt_date, bt_time)
        return {
            "status": "ok",
            "actions": actions,
            "portfolio": self.portfolio.get_portfolio_summary(prices),
        }

    def _write_cost_log(self, bt_date=None, bt_time=None):
        """Persist per-cycle LLM token usage + USD cost to cost.jsonl."""
        if not self.session:
            return
        usage = getattr(self.engine, "last_usage", None) or {}
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        if in_tok == 0 and out_tok == 0:
            return  # nothing happened (e.g. error before any LLM call)
        active_model = self._active_model
        try:
            from .llm_pricing import estimate_usd
            usd = estimate_usd(active_model, in_tok, out_tok)
        except Exception:
            usd = 0.0

        from datetime import datetime as _dt
        if bt_date and bt_time:
            ts = f"{bt_date}T{bt_time}:00"
        else:
            ts = _dt.now().isoformat(timespec="seconds")

        record = {
            "ts": ts,
            "model": active_model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "calls": int(usage.get("calls", 0) or 0),
            "usd": round(usd, 6),
        }
        try:
            log_path = self.session.session_dir / "cost.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                import json as _json
                f.write(_json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Could not write cost log: {e}")

    def _write_thinking_log(self, actions, bt_date=None, bt_time=None):
        """Persist the cycle's reasoning trail for transparency UI."""
        if not self.session:
            return
        trail = getattr(self.engine, "last_reasoning", None) or []
        # Build placed trades summary
        placed = []
        for a in actions:
            r = a.get("result") or {}
            if r.get("success") and r.get("action") in ("BUY", "SHORT", "SELL", "COVER"):
                placed.append({
                    "action": r.get("action"),
                    "ticker": r.get("ticker") or (a.get("input") or {}).get("ticker"),
                    "qty": r.get("quantity") or r.get("shares"),
                    "price": r.get("price"),
                })

        # Heuristic phase tag
        if placed:
            phase = "executed"
        elif any(c.get("name") == "place_trade" for it in trail for c in (it.get("tool_calls") or [])):
            phase = "rejected"  # tried but failed
        else:
            phase = "observed"

        from datetime import datetime as _dt
        if bt_date and bt_time:
            ts = f"{bt_date}T{bt_time}:00"
        else:
            ts = _dt.now().isoformat(timespec="seconds")

        record = {
            "ts": ts,
            "phase": phase,
            "iterations": len(trail),
            "trail": trail,
            "placed": placed,
        }
        try:
            log_path = self.session.session_dir / "thinking.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                import json as _json
                f.write(_json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Could not write thinking log: {e}")

    def run_daily_review(self):
        """Run end-of-day review and update learning journal."""
        logger.info("📝 Running daily review...")
        prices = self.market_data.get_current_prices()

        # Save daily snapshot
        self.portfolio.save_daily_snapshot(prices)

        # Generate and save reflection
        if hasattr(self.engine, 'client'):
            reflection = self.learner.generate_daily_review(self.engine.client)
            self.learner.write_reflection(reflection)
            # Synthesise ALL journal history into a persistent distilled rules block
            # so nothing is lost to truncation next session
            self.learner.update_distilled_rules(self.engine.client)

        # Log performance stats
        stats = self.learner.get_performance_stats()
        logger.info(f"📊 Performance: {json.dumps(stats)}")

    def run_loop(self, mode: str = "intraday"):
        """
        Run the agent continuously.
        mode: 'intraday' (every N minutes) or 'swing' (at scheduled times)
        """
        logger.info(f"🚀 Starting agent loop in {mode} mode")
        self._last_review_date: str | None = None

        try:
            while True:
                market_open = self.market_data.is_market_open()

                # For intraday scalping: wake up early for pre-market scan
                is_premarket_window = False
                if not market_open and self.preset and getattr(self.preset, 'use_premarket_scanner', False):
                    is_premarket_window = self._is_premarket_window()

                if not market_open and not is_premarket_window:
                    logger.info("💤 Market closed, waiting...")
                    time.sleep(60)
                    continue

                # Run pre-market scanner if in pre-market window (8:30-9:15)
                if is_premarket_window and not market_open:
                    self._maybe_run_premarket_scan()
                    logger.info("📋 Pre-market scan done. Waiting for market open...")
                    time.sleep(60)
                    continue

                # Run periodic daily review (every 24h for 24/7 markets, at market close for others)
                self._maybe_run_daily_review()

                # Run scanner at market open if not already done in pre-market
                self._maybe_run_premarket_scan()

                result = self.run_once()
                logger.info(f"Cycle result: {result['status']}")

                if mode == "intraday":
                    sleep_seconds = self.config.intraday_interval_min * 60
                else:
                    sleep_seconds = 30 * 60  # 30 min for swing

                logger.info(f"⏳ Next cycle in {sleep_seconds // 60} minutes")
                time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("🛑 Agent stopped by user")
            self.run_daily_review()
        except Exception as e:
            logger.error(f"💥 Agent crashed with unhandled exception: {e}", exc_info=True)
            raise

    def _maybe_run_daily_review(self):
        """
        Run daily review once per calendar day (UTC).
        For 24/7 markets (crypto), triggers at midnight UTC.
        For regular markets, triggers near market close.
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_review_date == today:
            return  # Already reviewed today

        is_24x7 = (
            self.market_data.market_preset
            and self.market_data.market_preset.is_24x7
        )

        if is_24x7:
            # For 24/7 markets: run review once per UTC day at midnight
            if self._last_review_date is not None and self._last_review_date != today:
                logger.info("🔄 Running scheduled daily review (24/7 market)")
                self.run_daily_review()
            self._last_review_date = today
        else:
            # For regular markets: run review once per day near market close
            # Use a separate flag so we don't miss the close window
            if not hasattr(self, '_review_done_today'):
                self._review_done_today = False

            if self._last_review_date != today:
                # New day — reset the flag
                self._review_done_today = False
                self._last_review_date = today

            if not self._review_done_today and self.risk_manager.check_intraday_close():
                logger.info("🔄 Running scheduled daily review (market closing)")
                self.run_daily_review()
                self._review_done_today = True

    def _is_premarket_window(self) -> bool:
        """Check if we're in the pre-market window (8:30 AM - market open)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = self.preset.timezone if self.preset else "Asia/Kolkata"
        now = datetime.now(ZoneInfo(tz))

        # Only on weekdays
        if now.weekday() > 4:
            return False

        # Pre-market: 8:30 AM to market open (9:15 AM default)
        premarket_start = now.replace(hour=8, minute=30, second=0, microsecond=0)
        open_str = self.preset.market_open if self.preset else "09:15"
        open_h, open_m = map(int, open_str.split(":"))
        market_open = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)

        return premarket_start <= now < market_open

    def _maybe_run_premarket_scan(self):
        """Run pre-market scanner once per day to build dynamic watchlist."""
        if not hasattr(self, '_scan_done_today'):
            self._scan_done_today = False
            self._scan_date = None

        from datetime import date
        today = date.today().isoformat()
        if self._scan_date == today and self._scan_done_today:
            return  # Already scanned today

        # Check if preset enables scanner
        use_scanner = (
            self.preset
            and getattr(self.preset, 'use_premarket_scanner', False)
            and hasattr(self.market_data, 'kite')  # Only works with Kite data
        )
        if not use_scanner:
            self._scan_done_today = True
            self._scan_date = today
            return

        try:
            from .premarket_scanner import PreMarketScanner
            scanner = PreMarketScanner(self.market_data.kite)

            # Get learnings and news for LLM-powered Phase 2
            learnings = self.learner.get_learnings(max_chars=3000) if hasattr(self, 'learner') else ""

            # Get LLM client for Phase 2 (intelligent stock selection)
            llm_client = getattr(self.engine, 'client', None) if hasattr(self, 'engine') else None

            scan_results = scanner.scan(
                max_stocks=25,
                llm_client=llm_client,
                llm_config=self.config,
                learnings=learnings,
                news="",  # News not available yet during pre-market
            )

            if scan_results:
                new_watchlist = [s["ticker"] for s in scan_results]
                old_count = len(self.config.watchlist)
                self.config.watchlist = new_watchlist
                self.market_data.watchlist = new_watchlist

                logger.info(f"🎯 Pre-market scan: replaced {old_count} stocks with {len(new_watchlist)} dynamic picks")
                for s in scan_results[:5]:
                    dir_icon = "📈" if s.get("llm_direction", "long") == "long" else "📉"
                    reason = s.get("llm_reason", s.get("reason", ""))[:50]
                    logger.info(f"  {dir_icon} {s['ticker']:15s} gap={s['gap_pct']:+5.1f}% — {reason}")

                # Build scan summary from the already-selected results (don't re-scan)
                lines = [f"## Pre-Market Picks — {len(scan_results)} Stocks Selected by AI"]
                for i, s in enumerate(scan_results):
                    direction = s.get("llm_direction", "?")
                    reason = s.get("llm_reason", s.get("reason", ""))
                    lines.append(f"  {i+1}. {s['ticker']} (Rs{s['ltp']}) gap={s['gap_pct']:+.1f}% [{direction}] — {reason}")
                self._premarket_summary = "\n".join(lines)
            else:
                logger.warning("Pre-market scan returned no results — keeping existing watchlist")
                self._premarket_summary = ""

        except Exception as e:
            logger.error(f"Pre-market scan failed: {e}")
            self._premarket_summary = ""

        self._scan_done_today = True
        self._scan_date = today

    def _load_directives(self) -> str:
        """Load active directives from directive.json for system prompt injection."""
        if not self.session:
            return ""
        directive_path = self.session.session_dir / "directive.json"
        if not directive_path.exists():
            return ""

        try:
            data = json.loads(directive_path.read_text())
            directives = data.get("directives", [])
        except Exception:
            return ""

        now = datetime.now()
        active = []
        expired_ids = []

        for d in directives:
            if d.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(d["expires_at"])
                    if now > exp:
                        expired_ids.append(d["id"])
                        continue
                except Exception:
                    pass
            active.append(d)

        # Clean up expired directives from file
        if expired_ids:
            data["directives"] = [d for d in data["directives"] if d["id"] not in expired_ids]
            directive_path.write_text(json.dumps(data, indent=2))

        if not active:
            return ""

        lines = []
        for d in active:
            expiry_label = {"this_cycle": "this cycle only", "today": "today", "until_cleared": "ongoing"}.get(d.get("expiry", ""), "")
            lines.append(f"- {d['text']} [{expiry_label}]")

        return "## 📋 Live Directives (from the operator — FOLLOW THESE)\n" + "\n".join(lines)

    def _cleanup_cycle_directives(self):
        """Remove 'this_cycle' directives after a cycle completes."""
        if not self.session:
            return
        directive_path = self.session.session_dir / "directive.json"
        if not directive_path.exists():
            return
        try:
            data = json.loads(directive_path.read_text())
            before = len(data["directives"])
            data["directives"] = [d for d in data["directives"] if d.get("expiry") != "this_cycle"]
            if len(data["directives"]) < before:
                directive_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _close_intraday_positions(self, prices: Dict[str, float]):
        """Force-close all intraday positions (longs and shorts) near market close."""
        positions = self.portfolio.get_open_positions()
        intraday = [p for p in positions if p.trade_type == "intraday"]

        for pos in intraday:
            price = prices.get(pos.ticker, pos.entry_price)
            if pos.direction == "short":
                logger.info(f"⏰ Force-covering intraday SHORT: {pos.ticker}")
                trade = self.portfolio.execute_cover(pos.id, price, reason="End-of-day forced cover", exit_type="forced_close")
            else:
                logger.info(f"⏰ Force-closing intraday LONG: {pos.ticker}")
                trade = self.portfolio.execute_sell(pos.id, price, reason="End-of-day forced close", exit_type="forced_close")
            self.learner.write_trade_log(trade, llm_client=self.engine.client)
