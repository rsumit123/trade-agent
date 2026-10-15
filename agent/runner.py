"""
Agent Runner — the main orchestrator that ties everything together.
Runs the Observe → Decide → Act → Learn loop on a schedule.
"""

import json
import logging
import math
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any
from pathlib import Path

from .config import AgentConfig
from .portfolio import Portfolio
from .market_data import MarketData, create_market_data, prices_incl_positions
from .web_research import WebResearcher
from .decision_engine import create_engine
from .risk_manager import RiskManager
from .learner import Learner
from . import trade_alerts

logger = logging.getLogger(__name__)

# Between decision cycles, open positions are checked against their stop/target
# this often, so a level touched mid-interval fills like a resting broker order
# instead of waiting up to `intraday_interval_min` for the next cycle.
MONITOR_INTERVAL_S = 60
# Kite quotes are real-time and cheap (one call for held tickers only), so stops
# are checked 4x as often there: a 60s check let fast moves fill ~1-2% past the stop.
MONITOR_INTERVAL_KITE_S = 15
# A failed/empty stock scan is retried on later cycles, this many tries per day.
MAX_SCAN_ATTEMPTS = 3


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

        # Exits the agent didn't decide itself (stop/target/forced close), reported
        # to it at the start of its next decision so it knows what happened.
        self._auto_exits: list[str] = []

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
        self.market_data = create_market_data(self.config, market_preset=self.preset)
        self.live_mirror = None
        self.portfolio = self._make_portfolio()
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

    def _make_portfolio(self) -> Portfolio:
        """Paper portfolio; for an allowlisted live session, one that also
        mirrors every booking as a real Groww order (agent/live_mirror.py).
        A session that is no longer live but still has real positions open
        today gets the mirror in exit-only mode, so they are still managed."""
        from . import live_mirror
        session = getattr(self, "session", None)
        self.live_worker = None
        live = live_mirror.live_enabled_for(session)
        exit_only = (not live and session is not None and not getattr(session, "backtest_mode", False)
                     and live_mirror.ledger_has_exposure(session.session_dir))
        if not (live or exit_only):
            return Portfolio(self.config.db_path, self.config.starting_capital)
        try:
            self.live_mirror = live_mirror.build_mirror(
                session, self.market_data, exit_only=exit_only,
                is_backtest=lambda: getattr(self, "_is_backtest", False),
                forced_close_time=lambda now: self.risk_manager.forced_close_time(now),
                db_path=self.config.db_path,
            )
            # Started only by run_loop: one-shot, --status and dashboard paths never
            # take the live lock or touch the broker (their events are dropped).
            self.live_worker = live_mirror.LiveWorker(self.live_mirror)
            mode = "EXIT-ONLY (real positions still open)" if exit_only else "ON"
            logger.warning(f"💸 LIVE MIRROR {mode}: paper trades of {session.session_id} "
                           f"are mirrored as real Groww orders")
            return live_mirror.LivePortfolio(self.config.db_path, self.config.starting_capital,
                                             self.live_worker)
        except Exception as e:
            logger.error(f"Live mirror setup failed — paper only: {e}")
            self.live_mirror = self.live_worker = None
            if exit_only or live:
                try:
                    from .health import send_telegram
                    send_telegram(f"[{session.session_id} LIVE] mirror setup FAILED: {e}. "
                                  f"Real positions are not managed — check Groww.")
                except Exception:
                    pass
            return Portfolio(self.config.db_path, self.config.starting_capital)

    def _start_live(self):
        """Agent loop only: take the live lock, start the broker worker, and
        handle SIGTERM (dashboard stop/liquidate) without dying mid-order."""
        worker = getattr(self, "live_worker", None)
        if worker is None:
            return
        worker.start()
        import signal
        import threading
        from . import live_mirror

        def on_term(signum, frame):
            liquidate = live_mirror.liquidation_requested(self.session.session_dir)
            logger.warning(f"🛑 SIGTERM — {'flattening real positions' if liquidate else 'protecting real positions'} "
                           f"before exit")
            worker.shutdown(liquidate=liquidate, timeout=60 if liquidate else 40)
            raise SystemExit(0)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, on_term)

    def _apply_live_universe_filter(self, tickers):
        """live_universe_filter: mis_only — keep only stocks Groww allows for
        intraday, so paper and real trade the same names. Other sessions: as is."""
        mirror = getattr(self, "live_mirror", None)
        if (getattr(self.session, "live_universe_filter", None) or "") != "mis_only" or mirror is None:
            return list(tickers)
        inst = mirror.instruments
        if inst is None or not inst.load():
            logger.warning("⚠️  Groww instrument list unavailable — universe NOT filtered (real entries "
                           "will be blocked per stock)")
            return list(tickers)
        keep, dropped = [], []
        for t in tickers:
            sym = t[:-3] if t.endswith(".NS") else t
            (keep if inst.intraday_allowed(sym) else dropped).append(t)
        if dropped:
            logger.info(f"🧹 Not intraday-allowed on Groww, dropped from the universe: {', '.join(dropped)}")
        return keep

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
            prices = prices_incl_positions(self.market_data, self.portfolio)
            return self.portfolio.get_portfolio_summary(prices)

        elif tool_name == "get_stock_details":
            ticker = tool_input.get("ticker", "")
            return self.market_data.get_stock_context(ticker)

        elif tool_name == "place_trade":
            result = self._execute_trade(tool_input)
            self._trade_alert(trade_alerts.format_trade, tool_input, result)
            return result

        elif tool_name == "update_levels":
            old = self._position_levels(tool_input.get("trade_id"))
            result = self._update_levels(tool_input)
            self._trade_alert(trade_alerts.format_levels, tool_input, result, *old)
            return result

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    def _position_levels(self, trade_id):
        """(stop, target) of an open position before it changes, for the alert."""
        try:
            pos = next((p for p in self.portfolio.get_open_positions() if p.id == int(trade_id)), None)
            return (pos.stop_price, pos.target_price) if pos else (None, None)
        except Exception:
            return (None, None)

    def _trade_alert(self, fmt, *args):
        """Telegram alert for a trade action, if the session opted in. Never raises."""
        if not getattr(self, "session", None) or getattr(self, "_is_backtest", False):
            return
        try:
            text = fmt(self.session.session_id, self.config.currency_symbol, *args)
            trade_alerts.send(self.session.session_dir, text)
        except Exception as e:
            logger.warning(f"Trade alert failed: {e}")

    def _update_levels(self, tool_input: Dict) -> Dict:
        """Adjust stop_price / target_price on an existing open position.
        At least one of the two must be provided. trade_id is required."""
        trade_id = tool_input.get("trade_id")
        if trade_id is None:
            return {"success": False, "error": "trade_id required"}
        new_stop = tool_input.get("stop_price")
        new_target = tool_input.get("target_price")
        reason = tool_input.get("reason", "") or ""
        if new_stop is None and new_target is None:
            return {"success": False, "error": "Provide stop_price and/or target_price"}
        try:
            updated = self.portfolio.update_levels(
                int(trade_id),
                stop_price=float(new_stop) if new_stop is not None else None,
                target_price=float(new_target) if new_target is not None else None,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}
        if not updated:
            return {"success": False, "error": f"Trade {trade_id} not open or not found"}
        logger.info(
            f"🔧 Levels updated for trade {trade_id} ({updated.ticker}): "
            f"stop={updated.stop_price}, target={updated.target_price} — {reason[:80]}"
        )
        return {
            "success": True,
            "action": "UPDATE_LEVELS",
            "trade_id": int(trade_id),
            "ticker": updated.ticker,
            "stop_price": updated.stop_price,
            "target_price": updated.target_price,
        }

    @property
    def _active_model(self) -> str:
        """Get the currently active LLM model name."""
        provider = self.config.llm_provider
        return getattr(self.config, f"{provider}_model", "unknown")

    def _check_quantity(self, quantity):
        """Return (quantity, error). Whole shares, or any positive amount on
        fractional markets; 0, negative, missing or non-numeric are refused."""
        fractional = bool(self.preset and getattr(self.preset, "fractional_qty", False))
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
            return None, f"Invalid quantity {quantity!r}: must be a positive {'number' if fractional else 'whole number'}"
        if quantity <= 0:
            return None, f"Invalid quantity {quantity!r}: must be greater than 0"
        if not fractional:
            if quantity != int(quantity):
                return None, f"Invalid quantity {quantity!r}: whole shares only"
            quantity = int(quantity)
        return quantity, None

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
            quantity, qty_err = self._check_quantity(trade_input.get("quantity"))
            if qty_err:
                logger.warning(f"❌ BUY rejected: {qty_err}")
                return {"success": False, "error": qty_err}
            trade_type = trade_input.get("trade_type", "swing")
            if self.preset and list(self.preset.trade_types) == ["intraday"]:
                if trade_type != "intraday":
                    logger.info(f"⏰ {ticker}: {self.preset.market_id} is intraday-only — booking as intraday (asked: {trade_type})")
                trade_type = "intraday"

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
            price = prices.get(pos.ticker)
            if not price:
                return {"success": False, "error": f"Cannot get price for {pos.ticker} — try again next cycle"}

            # A SHORT must be closed by covering, not selling. If the LLM issues
            # SELL on a short, route it to cover so cash/P&L stay correct.
            if (pos.direction or "").lower() == "short":
                logger.warning(f"↩️  SELL on SHORT {pos.ticker} (id {trade_id}) — routing to COVER")
                trade = self.portfolio.execute_cover(trade_id, price, reason)
                self.learner.write_trade_log(trade, llm_client=self.engine.client)
                return {
                    "success": True,
                    "trade_id": trade_id,
                    "action": "COVER",
                    "ticker": trade.ticker,
                    "exit_price": price,
                    "pnl": round(trade.pnl, 2),
                }

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
            if not ticker:
                return {"success": False, "error": "ticker and quantity required for SHORT"}
            quantity, qty_err = self._check_quantity(trade_input.get("quantity"))
            if qty_err:
                logger.warning(f"❌ Short rejected: {qty_err}")
                return {"success": False, "error": qty_err}

            # Get current price
            prices = self.market_data.get_current_prices([ticker])
            price = prices.get(ticker)
            if not price:
                return {"success": False, "error": f"Cannot get price for {ticker}"}

            # Risk check (reuse buy limits — same capital at risk)
            approved, risk_msg = self.risk_manager.check_buy(
                ticker, quantity, price, prices, action="SHORT"
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
            price = prices.get(pos.ticker)
            if not price:
                return {"success": False, "error": f"Cannot get price for {pos.ticker} — try again next cycle"}

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

    def _enrich_with_affordability(self, watchlist_data: list, portfolio_summary: dict) -> None:
        """Mutate each watchlist row to include `max_qty` — the largest qty the
        agent can buy at the current price without breaking cash, per-trade,
        or per-position caps. Removes the ambiguity that led to LLM proposing
        oversized trades and getting rejected post-hoc.
        """
        try:
            cash = float(portfolio_summary.get("cash", 0) or 0)
            equity = float(portfolio_summary.get("total_value", cash) or cash)
        except Exception:
            return

        if cash <= 0 or not watchlist_data:
            return

        per_pos_cap_pct = float(getattr(self.config, "max_position_pct", 0.20) or 0.20)
        per_trade_amt = getattr(self.config, "max_trade_amount", None)
        try:
            per_trade_amt = float(per_trade_amt) if per_trade_amt else None
        except Exception:
            per_trade_amt = None

        # Whole shares for stocks; crypto trades in fractions (one BTC costs
        # more than a $10k account's per-trade budget, so int() gave max_qty=0
        # and the crypto agents could never trade).
        fractional = bool(self.preset and getattr(self.preset, "fractional_qty", False))

        per_pos_cap = equity * per_pos_cap_pct
        budget = min(cash, per_pos_cap)
        if per_trade_amt and per_trade_amt > 0:
            budget = min(budget, per_trade_amt)

        for row in watchlist_data:
            try:
                price = float(row.get("current_price") or 0)
                if price <= 0:
                    continue
                row["max_qty"] = (math.floor(budget / price * 1e6) / 1e6 if fractional
                                  else int(budget // price))
            except Exception:
                continue

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
                prices = prices_incl_positions(self.market_data, self.portfolio)
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

    def _market_context(self, watchlist_data: list, is_backtest: bool) -> str:
        """Live Nifty / Bank Nifty / VIX / breadth block for NSE agents on Kite,
        and each watchlist row's strength vs Nifty. "" when unavailable."""
        if (is_backtest or getattr(self, "_is_backtest", False) or not self.preset
                or self.preset.is_24x7 or not hasattr(self.market_data, "_call_with_retry")):
            return ""
        from . import market_context
        from .sector_data import get_nifty50
        ctx = market_context.fetch(self.market_data, get_nifty50())
        market_context.add_relative_strength(watchlist_data, ctx)
        return market_context.format_context(ctx)

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
        prices = prices_incl_positions(self.market_data, self.portfolio)
        watchlist_data = self.market_data.get_watchlist_summary()

        # 2. Stop/target sweep — writes journal entry for each closed position.
        #    Runs before the risk status (so the prompt sees post-exit P&L/cash)
        #    and even when trading is blocked or data is down.
        stopped = self.risk_manager.run_stop_loss_sweep(prices)
        if stopped:
            logger.info(f"🛑 Stop/target triggered on {len(stopped)} positions")
            for trade in stopped:
                self.learner.write_trade_log(trade, llm_client=self.engine.client)
                self._note_auto_exit(trade)

        # 3. Force-close intraday positions near market close (≥15:15 IST)
        #    Skip during backtest — the backtest engine handles EOD close separately
        if not is_backtest:
            near_close = self.risk_manager.check_intraday_close()
            if near_close:
                self._close_intraday_positions(prices)
                # Don't let LLM open new positions when we're in the closing window
                logger.info("⏰ Near market close — skipping new trade decisions")
                return {"status": "ok", "actions": [], "portfolio": self.portfolio.get_portfolio_summary(prices)}

        # 4. Check risk status (after the sweep, so it reflects any exits above)
        risk_status = self.risk_manager.get_risk_status(prices)
        if not risk_status["can_trade"]:
            logger.warning("⚠️  Daily loss limit hit — no trading today")
            return {"status": "blocked", "reason": "daily loss limit"}

        # Kite outage: a watchlist with neither summary rows nor quotes would
        # pay for a full LLM cycle on "(no data)".
        if (not is_backtest and not getattr(self, "_is_backtest", False)
                and self.config.watchlist and not watchlist_data and not prices):
            logger.warning("⚠️  No market data (watchlist summary and quotes both empty) — skipping LLM this cycle")
            return {"status": "no_data", "actions": []}

        market_ctx = self._market_context(watchlist_data, is_backtest)

        # News fetch is per-session opt-out (experiment: does news improve
        # or hurt decisions?). Default behaviour (include_news=True) is unchanged.
        include_news = getattr(self.session, "include_news", True) if self.session else True
        if include_news:
            news = self.researcher.build_research_context(self.config.watchlist[:5])
        else:
            news = "News disabled for this session — rely on price action, technicals, and distilled rules only."
            logger.info("📰 News: DISABLED for this session")

        # Log a brief news summary so it's visible in the dashboard log panel
        if include_news and news and news != "No market news available at this time.":
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

        # 5. DECIDE + ACT — let the LLM make decisions
        logger.info("🧠 Running decision engine...")
        portfolio_summary = self.portfolio.get_portfolio_summary(prices)
        learnings = self.learner.get_learnings(max_chars=6000)

        # Inject per-ticker affordability so the LLM cannot propose oversized trades.
        self._enrich_with_affordability(watchlist_data, portfolio_summary)

        # Load live directives from operator and prepend to learnings
        directives_text = self._load_directives()
        if directives_text:
            logger.info(f"📋 Live directives active ({directives_text.count(chr(10))} items)")
            learnings = directives_text + "\n\n" + learnings

        # Tell the agent about positions closed without its decision since last cycle.
        auto_exits = list(self._auto_exits)
        if auto_exits:
            learnings = (
                "## Since your last decision\n"
                "These positions were closed automatically (not by you):\n"
                + "\n".join(auto_exits) + "\n\n" + learnings
            )

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
            market_context=market_ctx,
        )

        # A cycle where every LLM call failed is an error, not "ok" — otherwise an
        # agent whose model was delisted or whose credits ran out looks healthy.
        llm_error = getattr(self.engine, "last_error", None)
        llm_calls = int((getattr(self.engine, "last_usage", None) or {}).get("calls", 0) or 0)
        llm_failed = bool(llm_error) and llm_calls == 0
        if self.session and not getattr(self, "_is_backtest", False):
            from .health import record_cycle
            record_cycle(self.session.session_dir, self.session.session_id,
                         self._active_model, llm_failed, llm_error)
        if llm_failed:
            logger.error(f"❌ Cycle failed — LLM unavailable: {llm_error}")
            return {"status": "llm_error", "error": llm_error, "actions": []}

        # The agent has now seen these exits.
        del self._auto_exits[:len(auto_exits)]

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
        cache_w = int(usage.get("cache_write_tokens", 0) or 0)
        cache_w1h = int(usage.get("cache_write_1h_tokens", 0) or 0)
        cache_r = int(usage.get("cache_read_tokens", 0) or 0)
        if in_tok == 0 and out_tok == 0 and cache_w == 0 and cache_r == 0:
            return  # nothing happened (e.g. error before any LLM call)
        active_model = self._active_model
        try:
            from .llm_pricing import estimate_usd
            usd = estimate_usd(active_model, in_tok, out_tok, cache_w, cache_r, cache_w1h)
        except Exception:
            usd = 0.0

        from datetime import datetime as _dt, timezone as _tz
        if bt_date and bt_time:
            ts = f"{bt_date}T{bt_time}:00"
        else:
            # UTC-aware so the browser interprets correctly (naive ISO was
            # being read as user-local time, mismatching the agent's IST clock)
            ts = _dt.now(_tz.utc).isoformat(timespec="seconds")

        record = {
            "ts": ts,
            "model": active_model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_write_tokens": cache_w,
            "cache_write_1h_tokens": cache_w1h,
            "cache_read_tokens": cache_r,
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

        from datetime import datetime as _dt, timezone as _tz
        if bt_date and bt_time:
            ts = f"{bt_date}T{bt_time}:00"
        else:
            # UTC-aware ISO so the browser localizes correctly
            ts = _dt.now(_tz.utc).isoformat(timespec="seconds")

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
        prices = prices_incl_positions(self.market_data, self.portfolio)

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
        self._start_live()

        try:
            while True:
                market_open = self.market_data.is_market_open()

                if not market_open:
                    # Regular markets review once after the close, when every
                    # intraday position has been force-closed.
                    self._maybe_run_daily_review()
                    logger.info("💤 Market closed, waiting...")
                    time.sleep(60)
                    continue

                # Run periodic daily review (every 24h for 24/7 markets, at market close for others)
                self._maybe_run_daily_review()

                # Stock scan runs at the open, never before: until then Kite
                # quotes still carry yesterday's candle (see _maybe_run_premarket_scan).
                self._maybe_run_premarket_scan()

                result = self.run_once()
                logger.info(f"Cycle result: {result['status']}")

                if mode == "intraday":
                    sleep_seconds = self.config.intraday_interval_min * 60
                else:
                    sleep_seconds = 30 * 60  # 30 min for swing

                logger.info(f"⏳ Next cycle in {sleep_seconds // 60} minutes")
                self._sleep_monitoring_positions(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("🛑 Agent stopped by user")
            self.run_daily_review()
        except Exception as e:
            logger.error(f"💥 Agent crashed with unhandled exception: {e}", exc_info=True)
            raise

    def _seconds_to_forced_close(self):
        """Seconds until the intraday forced-close time today, or None when there
        is none (24/7 market, backtest) or it has already passed."""
        if getattr(self, "_is_backtest", False):
            return None
        try:
            now = datetime.now(ZoneInfo(self.config.timezone))
            trigger = self.risk_manager.forced_close_time(now)
        except Exception:
            return None
        if trigger is None:
            return None
        left = (trigger - now).total_seconds()
        return left if left > 0 else None

    def _sleep_monitoring_positions(self, sleep_seconds: float):
        """Sleep until the next cycle, checking open positions' stops/targets every 15s (Kite) or minute."""
        deadline = time.monotonic() + sleep_seconds
        # Per-agent jitter so agents sharing one Kite key don't quote in the same second.
        if hasattr(getattr(self, "market_data", None), "_call_with_retry"):     # Kite
            step = MONITOR_INTERVAL_KITE_S + random.uniform(0, 3)
        else:
            step = MONITOR_INTERVAL_S + random.uniform(0, 10)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            nap = min(step, remaining)
            # Wake exactly at the forced-close time rather than up to a step late.
            to_close = self._seconds_to_forced_close()
            if to_close is not None:
                nap = min(nap, to_close + 0.5)
            time.sleep(nap)
            if deadline - time.monotonic() > 1:
                self._monitor_positions()

    def _monitor_positions(self):
        """One stop/target check for open positions, plus the end-of-day forced
        close of intraday positions. No LLM call; never raises."""
        if getattr(self, "_is_backtest", False):
            return
        try:
            positions = self.portfolio.get_open_positions()
            if not positions or not self.market_data.is_market_open():
                return
            tickers = sorted({p.ticker for p in positions})
            prices = self.market_data.get_current_prices(tickers)
            if not prices:
                return                      # quote failed / rate-limited: try next minute
            closed = self.risk_manager.run_stop_loss_sweep(prices)
            for trade in closed:
                logger.info(f"⏱️  Between-cycle exit: {trade.ticker} ({trade.exit_type})")
                self.learner.write_trade_log(trade, llm_client=self.engine.client)
                self._note_auto_exit(trade)
            # The next decision cycle may only start after the close, so the
            # forced close must not wait for one.
            if self.risk_manager.check_intraday_close():
                self._close_intraday_positions(prices)
        except Exception as e:
            logger.warning(f"Position monitor skipped this minute: {e}")

    def _note_auto_exit(self, trade):
        """Queue a one-line note about an exit the agent didn't make, for its next prompt."""
        if trade is None or trade.exit_price is None:
            return
        self._trade_alert(trade_alerts.format_auto_exit, trade)
        label = {
            "stop_hit": "was STOPPED OUT",
            "target_hit": "hit its TARGET",
            "forced_close": "was force-closed before market close",
        }.get(trade.exit_type, "was closed")
        try:
            tz = ZoneInfo(self.preset.timezone) if self.preset else None
        except Exception:
            tz = None
        now = datetime.now(tz)
        sym = self.config.currency_symbol
        pnl = trade.pnl or 0.0
        self._auto_exits.append(
            f"- {trade.ticker} {trade.direction} ({trade.quantity} @ {sym}{trade.entry_price:,.2f}) "
            f"{label} at {sym}{trade.exit_price:,.2f} ({now:%H:%M} {now.tzname() or ''}). "
            f"P&L {sym}{pnl:+,.2f}. Reason: {trade.exit_reason}".rstrip()
        )

    def _maybe_run_daily_review(self, now=None):
        """
        Run the daily review once per day.
        For 24/7 markets (crypto), triggers at midnight UTC.
        For regular markets, triggers once after the close on weekdays.
        """
        from datetime import datetime, timezone

        is_24x7 = (
            self.market_data.market_preset
            and self.market_data.market_preset.is_24x7
        )

        if is_24x7:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_review_date == today:
                return  # Already reviewed today
            # For 24/7 markets: run review once per UTC day at midnight
            if self._last_review_date is not None and self._last_review_date != today:
                logger.info("🔄 Running scheduled daily review (24/7 market)")
                self.run_daily_review()
            self._last_review_date = today
            return

        # Regular markets. This used to share the early return above, which
        # marked the day done on the first (morning) loop, so the close-time
        # review only ran when an agent happened to start after the close.
        now = now or datetime.now(ZoneInfo(self.config.timezone))
        today = now.strftime("%Y-%m-%d")
        if now.weekday() >= 5 or self.market_data.is_market_open():
            return
        if not self.risk_manager.check_intraday_close(now):
            return  # before today's close (pre-market)
        if self._review_marker_date() == today:
            return
        logger.info("🔄 Running scheduled daily review (market closed)")
        self.run_daily_review()
        if getattr(self, "live_worker", None) is not None:
            self.live_worker.submit("send_eod_report")
        self._set_review_marker(today)

    def _review_marker_path(self):
        return self.session.session_dir / "last_daily_review" if self.session else None

    def _review_marker_date(self):
        """Date of the last close-time review; on disk so a restart doesn't repeat it."""
        path = self._review_marker_path()
        if path is None:
            return getattr(self, "_review_done_date", None)
        try:
            return path.read_text().strip()
        except OSError:
            return None

    def _set_review_marker(self, today: str):
        self._review_done_date = today
        path = self._review_marker_path()
        if path is not None:
            try:
                path.write_text(today)
            except OSError as e:
                logger.warning(f"Could not record daily review date: {e}")

    def _maybe_run_premarket_scan(self):
        """Run pre-market scanner once per day to build dynamic watchlist."""
        if not hasattr(self, '_scan_done_today'):
            self._scan_done_today = False
            self._scan_date = None

        from datetime import date
        today = date.today().isoformat()
        if self._scan_date == today and self._scan_done_today:
            return  # Already scanned today

        # New trading day: reload session config from disk so overnight
        # universe edits take effect at the next open without a restart.
        if self.session is not None:
            try:
                from .session import load_session
                fresh = load_session(self.session.session_id)
                self.session.universe = fresh.universe
            except Exception as e:
                logger.warning(f"Universe reload failed, keeping in-memory value: {e}")

            # Fixed universe → use the user's exact picks; never run the scanner.
            mode, tickers = self.session.resolve_universe()
            if mode == "fixed":
                tickers = self._apply_live_universe_filter(tickers)
                self.config.watchlist = list(tickers)
                self.market_data.watchlist = list(tickers)
                self._premarket_summary = (
                    f"## Trading Universe — {len(tickers)} stocks chosen by the user\n"
                    + "\n".join(f"  - {t}" for t in tickers)
                )
                self._scan_done_today = True
                self._scan_date = today
                logger.info(f"📌 Fixed universe: trading {len(tickers)} user-selected stocks (scanner skipped)")
                return

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

        # Restarted mid-day: resume today's picks. A fresh scan after the open
        # ranks stocks that have already made their move.
        saved = self.session.read_scan_picks(today) if self.session is not None else None
        if saved:
            saved["tickers"] = self._apply_live_universe_filter(saved["tickers"])
            self.config.watchlist = list(saved["tickers"])
            self.market_data.watchlist = list(saved["tickers"])
            self._premarket_summary = saved.get("summary", "")
            self._scan_done_today = True
            self._scan_date = today
            logger.info(f"🎯 Resumed today's {len(saved['tickers'])} pre-market picks (no re-scan)")
            return

        # Before the open, Kite quotes still hold yesterday's candle: the
        # "gap" comes out as yesterday's open vs close, so a stock that rallied
        # 8% yesterday is ranked as an 8% gap-down (2026-10-14 scan). Wait.
        if not self.market_data.is_market_open():
            return

        try:
            from .premarket_scanner import PreMarketScanner
            scanner = PreMarketScanner(
                self.market_data.kite,
                auth=getattr(self.market_data, "_auth", None),
            )

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
                kept = set(self._apply_live_universe_filter([s["ticker"] for s in scan_results]))
                scan_results = [s for s in scan_results if s["ticker"] in kept]
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
                try:
                    scanned = datetime.now(ZoneInfo(self.config.timezone))
                    stamp = f"{scanned:%H:%M} {scanned.tzname() or ''}".strip()
                except Exception:
                    stamp = datetime.now().strftime("%H:%M")
                lines = [
                    f"## Scanner Picks — {len(scan_results)} stocks selected by AI at {stamp}",
                    "  (prices and gap % are as of that scan, not live — see the watchlist table for current prices)",
                ]
                for i, s in enumerate(scan_results):
                    direction = s.get("llm_direction", "?")
                    reason = s.get("llm_reason", s.get("reason", ""))
                    lines.append(f"  {i+1}. {s['ticker']} (Rs{s['ltp']}) gap={s['gap_pct']:+.1f}% [{direction}] — {reason}")
                self._premarket_summary = "\n".join(lines)
                if self.session is not None:
                    try:
                        self.session.write_scan_picks(new_watchlist, self._premarket_summary)
                    except Exception as e:
                        logger.warning(f"Could not persist scan picks: {e}")
                self._scan_done_today = True
                self._scan_date = today
                return
            else:
                logger.warning("Pre-market scan returned no results — keeping existing watchlist")
                self._premarket_summary = ""

        except Exception as e:
            logger.error(f"Pre-market scan failed: {e}")
            self._premarket_summary = ""

        # Failed or empty: retry on the next cycles rather than trading the stale
        # default universe all day; give up after MAX_SCAN_ATTEMPTS.
        if getattr(self, "_scan_attempt_date", None) != today:
            self._scan_attempt_date, self._scan_attempts = today, 0
        self._scan_attempts += 1
        if self._scan_attempts >= MAX_SCAN_ATTEMPTS:
            logger.error(f"Pre-market scan failed {self._scan_attempts}x today — keeping the existing watchlist")
            self._scan_done_today = True
            self._scan_date = today
        else:
            logger.warning(f"Pre-market scan attempt {self._scan_attempts}/{MAX_SCAN_ATTEMPTS} failed — will retry next cycle")

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
        """Force-close all intraday positions (longs and shorts) near market close.
        Live prices only: a position with no quote is skipped (the between-cycle
        monitor retries) rather than booked at its entry price."""
        positions = self.portfolio.get_open_positions()
        intraday = [p for p in positions if p.trade_type == "intraday"]

        for pos in intraday:
            price = prices.get(pos.ticker)
            if not price:
                logger.warning(f"⚠️  No live price for {pos.ticker} (id {pos.id}) — forced close deferred, retrying")
                continue
            if pos.direction == "short":
                logger.info(f"⏰ Force-covering intraday SHORT: {pos.ticker}")
                trade = self.portfolio.execute_cover(pos.id, price, reason="End-of-day forced cover", exit_type="forced_close")
            else:
                logger.info(f"⏰ Force-closing intraday LONG: {pos.ticker}")
                trade = self.portfolio.execute_sell(pos.id, price, reason="End-of-day forced close", exit_type="forced_close")
            self.learner.write_trade_log(trade, llm_client=self.engine.client)
            self._note_auto_exit(trade)
