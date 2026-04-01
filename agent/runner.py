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
from .market_data import MarketData
from .web_research import WebResearcher
from .decision_engine import create_engine
from .risk_manager import RiskManager
from .learner import Learner

logger = logging.getLogger(__name__)


class TradingAgent:
    """
    The autonomous trading agent.
    Call `run_once()` for a single decision cycle, or `run_loop()` for continuous operation.
    """

    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()

        # Ensure directories exist
        for path in [self.config.db_path, self.config.learnings_path, self.config.log_path]:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(self.config.log_path),
                logging.StreamHandler(),
            ]
        )

        # Initialize components
        self.portfolio = Portfolio(self.config.db_path, self.config.starting_capital)
        self.market_data = MarketData(self.config.watchlist)
        self.researcher = WebResearcher(self.config.news_sources)
        self.risk_manager = RiskManager(self.config, self.portfolio)
        self.learner = Learner(self.config, self.portfolio)
        self.engine = create_engine(self.config)

        logger.info("🤖 Trading Agent initialized")
        logger.info(f"   Capital: ₹{self.config.starting_capital:,.0f}")
        logger.info(f"   Watchlist: {len(self.config.watchlist)} stocks")
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
            # Log top result so it's visible in dashboard
            if results:
                top = results[0]
                logger.info(f"📰 Search result: [{top.get('source','')}] {top.get('title','')[:80]}")
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

    def _execute_trade(self, trade_input: Dict) -> Dict:
        """Execute a trade with risk checks."""
        action = trade_input.get("action", "")
        reason = trade_input.get("reason", "")

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

            # Execute
            trade = self.portfolio.execute_buy(ticker, quantity, price, trade_type, reason)
            self.learner.write_trade_log(trade)
            return {
                "success": True,
                "trade_id": trade.id,
                "action": "BUY",
                "ticker": ticker,
                "quantity": quantity,
                "price": price,
                "total_cost": round(quantity * price, 2),
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

        return {"success": False, "error": f"Unknown action: {action}"}

    # ── Main Loop ────────────────────────────────────────────

    def run_once(self, force_intraday: bool = False) -> Dict:
        """Run a single decision cycle.
        force_intraday: hint the LLM to prefer intraday trades (useful for testing).
        """
        logger.info("=" * 60)
        logger.info("🔄 Starting decision cycle" + (" [FORCE INTRADAY]" if force_intraday else ""))

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

        # 3. Run stop-loss sweep
        stopped = self.risk_manager.run_stop_loss_sweep(prices)
        if stopped:
            logger.info(f"🛑 Stop-loss triggered on {len(stopped)} positions")

        # 4. Force-close intraday positions near market close
        if self.risk_manager.check_intraday_close():
            self._close_intraday_positions(prices)

        # 5. DECIDE + ACT — let the LLM make decisions
        logger.info("🧠 Running decision engine...")
        portfolio_summary = self.portfolio.get_portfolio_summary(prices)
        learnings = self.learner.get_learnings()

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
        )

        logger.info(f"✅ Cycle complete — {len(actions)} actions taken")
        return {
            "status": "ok",
            "actions": actions,
            "portfolio": self.portfolio.get_portfolio_summary(prices),
        }

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

        # Log performance stats
        stats = self.learner.get_performance_stats()
        logger.info(f"📊 Performance: {json.dumps(stats)}")

    def run_loop(self, mode: str = "intraday"):
        """
        Run the agent continuously.
        mode: 'intraday' (every N minutes) or 'swing' (at scheduled times)
        """
        logger.info(f"🚀 Starting agent loop in {mode} mode")

        try:
            while True:
                if not self.market_data.is_market_open():
                    logger.info("💤 Market closed, waiting...")
                    time.sleep(60)  # Check every minute
                    continue

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

    def _close_intraday_positions(self, prices: Dict[str, float]):
        """Force-close all intraday positions near market close."""
        positions = self.portfolio.get_open_positions()
        intraday = [p for p in positions if p.trade_type == "intraday"]

        for pos in intraday:
            price = prices.get(pos.ticker, pos.entry_price)
            logger.info(f"⏰ Force-closing intraday: {pos.ticker}")
            trade = self.portfolio.execute_sell(pos.id, price, reason="End-of-day forced close")
            self.learner.write_trade_log(trade, llm_client=self.engine.client)
