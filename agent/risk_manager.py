"""
Risk Manager — enforces hard trading limits.
These rules CANNOT be overridden by the LLM. They are the guardrails.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from .config import AgentConfig
from .portfolio import Portfolio

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Hard risk checks that gate every trade.
    The LLM proposes, the RiskManager disposes.
    """

    def __init__(self, config: AgentConfig, portfolio: Portfolio):
        self.config = config
        self.portfolio = portfolio

    @property
    def _sym(self) -> str:
        """Currency symbol from market preset, or ₹ by default."""
        return self.config.currency_symbol

    def check_buy(self, ticker: str, quantity: int, price: float,
                  current_prices: Dict[str, float] = None) -> Tuple[bool, str]:
        """
        Validate a BUY order against all risk limits.
        Returns (approved: bool, reason: str)
        """
        trade_value = quantity * price
        cash = self.portfolio.get_cash()
        summary = self.portfolio.get_portfolio_summary(current_prices)

        # 1. Sufficient cash
        if trade_value > cash:
            return False, f"Insufficient cash: need {self._sym}{trade_value:.0f}, have {self._sym}{cash:.0f}"

        # 2. Max trade amount
        if trade_value > self.config.max_trade_amount:
            return False, f"Trade {self._sym}{trade_value:.0f} exceeds max {self._sym}{self.config.max_trade_amount:.0f}"

        # 3. Position size limit
        total_value = summary["total_value"]
        position_pct = trade_value / total_value
        if position_pct > self.config.max_position_pct:
            return False, (
                f"Position {position_pct:.1%} exceeds max {self.config.max_position_pct:.1%} "
                f"of portfolio"
            )

        # 4. Check existing exposure to same ticker
        existing_value = sum(
            h["market_value"] for h in summary["holdings"]
            if h["ticker"] == ticker
        )
        combined_pct = (existing_value + trade_value) / total_value
        if combined_pct > self.config.max_position_pct:
            return False, (
                f"Combined {ticker} exposure {combined_pct:.1%} would exceed "
                f"{self.config.max_position_pct:.1%} limit"
            )

        # 5. Max open positions
        if summary["open_positions"] >= self.config.max_open_positions:
            return False, f"Max open positions ({self.config.max_open_positions}) reached"

        # 6. Daily loss limit — stop trading if breached
        daily_loss_limit = self.config.starting_capital * self.config.daily_loss_limit_pct
        if summary["today_pnl"] < -daily_loss_limit:
            return False, (
                f"Daily loss limit breached: today's P&L {self._sym}{summary['today_pnl']:.0f} "
                f"exceeds -{self._sym}{daily_loss_limit:.0f}"
            )

        logger.info(f"✅ BUY approved: {quantity}x {ticker} @ {self._sym}{price:.2f} ({self._sym}{trade_value:.0f})")
        return True, "Approved"

    def check_sell(self, trade_id: int) -> Tuple[bool, str]:
        """Validate a SELL (close long position) — mostly just verify the trade exists."""
        positions = self.portfolio.get_open_positions()
        trade = next((t for t in positions if t.id == trade_id), None)

        if not trade:
            return False, f"No open trade found with id {trade_id}"

        return True, "Approved"

    def check_cover(self, trade_id: int) -> Tuple[bool, str]:
        """Validate a COVER (close short position) — verify the short trade exists."""
        positions = self.portfolio.get_open_positions()
        trade = next(
            (t for t in positions if t.id == trade_id and getattr(t, "direction", "long") == "short"),
            None
        )
        if not trade:
            return False, f"No open SHORT trade found with id {trade_id}"
        return True, "Approved"

    def should_stop_loss(self, trade_id: int, current_price: float) -> Tuple[bool, str]:
        """Check if a position should be stopped out."""
        positions = self.portfolio.get_open_positions()
        trade = next((t for t in positions if t.id == trade_id), None)

        if not trade:
            return False, "Trade not found"

        loss_pct = (current_price - trade.entry_price) / trade.entry_price
        if loss_pct < -self.config.per_trade_loss_limit_pct:
            return True, (
                f"Stop-loss triggered: {trade.ticker} down {loss_pct:.2%} "
                f"(limit: {self.config.per_trade_loss_limit_pct:.2%})"
            )

        return False, "Within limits"

    def check_intraday_close(self) -> bool:
        """Check if it's time to force-close intraday positions (near market close).
        Returns False for 24/7 markets (no forced close)."""
        preset = self.config._market_preset
        if preset and preset.is_24x7:
            return False

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(self.config.timezone))

        close_str = self.config.market_close  # e.g. "15:30"
        buffer = preset.intraday_close_buffer_min if preset else 15
        close_h, close_m = map(int, close_str.split(":"))
        market_close = now.replace(hour=close_h, minute=close_m, second=0)
        trigger_time = market_close - timedelta(minutes=buffer)
        return now >= trigger_time

    def run_stop_loss_sweep(self, current_prices: Dict[str, float]):
        """
        Check all open positions for stop-loss OR target triggers.

        Branches:
        1. New LONG positions — ATR-derived stop_price + target_price.
           → Sell if price <= stop_price (stop) or price >= target_price (target)
        2. New SHORT positions — ATR-derived stop_price + target_price (reversed).
           → Cover if price >= stop_price (price rose against us) or price <= target_price (target hit)
        3. Legacy positions — no stored levels.
           → Fall back to config per_trade_loss_limit_pct stop only (longs only)
        """
        positions = self.portfolio.get_open_positions()
        closed = []

        for pos in positions:
            price = current_prices.get(pos.ticker)
            if not price:
                continue

            is_short = getattr(pos, "direction", "long") == "short"

            if pos.stop_price is not None and pos.target_price is not None:
                if is_short:
                    # ── Short: stop = price rises above stop_price ──────
                    if price >= pos.stop_price:
                        reason = (
                            f"Short stop hit: {pos.ticker} @ {self._sym}{price:.2f} "
                            f"≥ stop {self._sym}{pos.stop_price:.2f}"
                        )
                        logger.warning(f"🛑 SHORT STOP: {reason}")
                        trade = self.portfolio.execute_cover(pos.id, price, reason=reason, exit_type="stop_hit")
                        closed.append(trade)
                    elif price <= pos.target_price:
                        reason = (
                            f"Short target hit: {pos.ticker} @ {self._sym}{price:.2f} "
                            f"≤ target {self._sym}{pos.target_price:.2f}"
                        )
                        logger.info(f"🎯 SHORT TARGET: {reason}")
                        trade = self.portfolio.execute_cover(pos.id, price, reason=reason, exit_type="target_hit")
                        closed.append(trade)
                else:
                    # ── Long: stop = price falls below stop_price ────────
                    if price <= pos.stop_price:
                        reason = (
                            f"Stop hit: {pos.ticker} @ {self._sym}{price:.2f} "
                            f"≤ stop {self._sym}{pos.stop_price:.2f}"
                        )
                        logger.warning(f"🛑 STOP: {reason}")
                        trade = self.portfolio.execute_sell(pos.id, price, reason=reason, exit_type="stop_hit")
                        closed.append(trade)
                    elif price >= pos.target_price:
                        reason = (
                            f"Target hit: {pos.ticker} @ {self._sym}{price:.2f} "
                            f"≥ target {self._sym}{pos.target_price:.2f}"
                        )
                        logger.info(f"🎯 TARGET: {reason}")
                        trade = self.portfolio.execute_sell(pos.id, price, reason=reason, exit_type="target_hit")
                        closed.append(trade)
            else:
                # ── Branch 3: legacy % stop (longs only) ───────────────
                if not is_short:
                    should_stop, reason = self.should_stop_loss(pos.id, price)
                    if should_stop:
                        logger.warning(f"🛑 STOP (legacy %): {reason}")
                        trade = self.portfolio.execute_sell(pos.id, price, reason=f"Stop-loss: {reason}")
                        closed.append(trade)

        return closed

    def get_risk_status(self, current_prices: Dict[str, float] = None) -> Dict:
        """Get current risk metrics for LLM context."""
        summary = self.portfolio.get_portfolio_summary(current_prices)
        daily_limit = self.config.starting_capital * self.config.daily_loss_limit_pct

        return {
            "daily_pnl": summary["today_pnl"],
            "daily_loss_limit": round(-daily_limit, 2),
            "daily_limit_used_pct": round(
                abs(min(summary["today_pnl"], 0)) / daily_limit * 100, 1
            ) if summary["today_pnl"] < 0 else 0,
            "open_positions": summary["open_positions"],
            "max_positions": self.config.max_open_positions,
            "can_trade": summary["today_pnl"] > -daily_limit,
            "cash_available": summary["cash"],
            "max_trade_amount": self.config.max_trade_amount,
        }
