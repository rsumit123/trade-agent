"""
Learner Module — reviews past trades and writes reflections to a markdown journal.
The agent learns from its wins and losses and feeds insights back into future decisions.
"""

import json
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from pathlib import Path

from .config import AgentConfig
from .portfolio import Portfolio, Trade

logger = logging.getLogger(__name__)


class Learner:
    """
    Manages the agent's learning journal.
    After each trading day, asks the LLM to reflect on trades taken.
    Learnings are fed back into the system prompt for future decisions.
    """

    def __init__(self, config: AgentConfig, portfolio: Portfolio):
        self.config = config
        self.portfolio = portfolio
        # Resolve to absolute path relative to project root (parent of this file's dir)
        _project_root = Path(__file__).resolve().parent.parent
        self.journal_path = (_project_root / config.learnings_path).resolve()
        self._ensure_journal()

    def _ensure_journal(self):
        """Create the journal file if it doesn't exist."""
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.journal_path.exists():
            self.journal_path.write_text(
                "# 🧠 Trading Agent — Learning Journal\n\n"
                "This file is automatically updated by the AI trading agent.\n"
                "It contains reflections on past trades and evolving strategies.\n\n"
                "---\n\n"
            )

    def get_learnings(self, max_chars: int = 3000) -> str:
        """Read recent learnings to feed back into the LLM context."""
        content = self.journal_path.read_text()

        # Return the last N characters (most recent entries)
        if len(content) > max_chars:
            # Find a clean break point
            truncated = content[-max_chars:]
            # Start from the next entry header
            entry_start = truncated.find("\n## ")
            if entry_start > 0:
                truncated = truncated[entry_start:]
            return f"[... earlier entries truncated ...]\n{truncated}"

        return content

    def generate_daily_review(self, llm_client) -> str:
        """
        Ask the LLM to review today's trades and generate learnings.
        Returns the reflection text that gets appended to the journal.
        """
        today_trades = self.portfolio.get_today_trades()
        recent_closed = self.portfolio.get_closed_trades(limit=10)
        summary = self.portfolio.get_portfolio_summary()

        if not today_trades and not recent_closed:
            return ""

        # Build review context
        trades_data = []
        for t in today_trades:
            trades_data.append({
                "ticker": t.ticker,
                "action": t.action,
                "type": t.trade_type,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "status": t.status,
                "reason": t.reason,
                "exit_reason": t.exit_reason,
            })

        review_prompt = f"""Review today's trading activity and write a brief learning reflection.

## Today's Trades
```json
{json.dumps(trades_data, indent=2)}
```

## Portfolio Summary
```json
{json.dumps(summary, indent=2)}
```

## Previous Learnings
{self.get_learnings(max_chars=1500)}

---

Write a concise reflection (3-5 bullet points) covering:
1. What worked well today and why
2. What didn't work and what you'd do differently
3. Any patterns you notice across recent trades
4. One specific rule or insight to apply going forward

Format as markdown. Be honest and specific — vague platitudes are useless.
Start with `## {date.today().strftime("%Y-%m-%d")} — Daily Review`
"""

        try:
            if hasattr(llm_client, 'messages') and self.config.llm_provider == "anthropic":
                # Anthropic native client
                response = llm_client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=1000,
                    messages=[{"role": "user", "content": review_prompt}],
                )
                reflection = response.content[0].text
            else:
                # OpenAI-compatible client (OpenRouter or OpenAI)
                model = (
                    self.config.openrouter_model
                    if self.config.llm_provider == "openrouter"
                    else self.config.openai_model
                )
                response = llm_client.chat.completions.create(
                    model=model,
                    max_tokens=1000,
                    messages=[{"role": "user", "content": review_prompt}],
                )
                reflection = response.choices[0].message.content

            return reflection

        except Exception as e:
            logger.error(f"Failed to generate review: {e}")
            return self._fallback_review(trades_data, summary)

    def _fallback_review(self, trades: List[Dict], summary: Dict) -> str:
        """Generate a basic review without LLM if API fails."""
        today = date.today().strftime("%Y-%m-%d")
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
        total_pnl = sum(t.get("pnl") or 0 for t in trades)

        return f"""## {today} — Daily Review (auto-generated)

- **Trades taken**: {len(trades)} (Wins: {wins}, Losses: {losses})
- **Day P&L**: ₹{total_pnl:.2f}
- **Portfolio value**: ₹{summary['total_value']:.2f}
- *Note: LLM review unavailable, this is a stats-only summary*
"""

    def write_reflection(self, reflection: str):
        """Append a reflection entry to the journal."""
        if not reflection.strip():
            return

        with open(self.journal_path, "a") as f:
            f.write(f"\n{reflection}\n\n---\n\n")

        logger.info(f"📝 Learning journal updated: {self.journal_path}")

    def write_trade_log(self, trade: Trade, llm_client=None):
        """
        Log a trade to the journal.
        - BUY: brief entry note (thesis captured at decision time).
        - SELL: LLM-generated reflection on what happened and what to learn.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        if trade.action == "BUY" or trade.exit_price is None:
            # ── Entry log ─────────────────────────────────────────────
            entry = (
                f"**ENTRY** {trade.quantity}x {trade.ticker} @ ₹{trade.entry_price:.2f}"
                f"  [{trade.trade_type}]\n"
                f"  *Thesis: {trade.reason or 'no reason recorded'}*"
            )
            logger.info(f"Trade logged: {entry}")
            journal_entry = f"### {timestamp} — Entry: {trade.ticker}\n{entry}\n"

        else:
            # ── Exit: generate LLM reflection ─────────────────────────
            pnl = trade.pnl or 0
            pnl_pct = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100
            outcome = "WIN ✅" if pnl > 0 else "LOSS ❌"
            hold_time = ""
            if trade.entry_time and trade.exit_time:
                try:
                    t0 = datetime.fromisoformat(trade.entry_time)
                    t1 = datetime.fromisoformat(trade.exit_time)
                    mins = int((t1 - t0).total_seconds() / 60)
                    hold_time = f"{mins}m" if mins < 120 else f"{mins//60}h{mins%60}m"
                except Exception:
                    pass

            summary_line = (
                f"**EXIT** {trade.quantity}x {trade.ticker} | "
                f"{outcome} | ₹{trade.entry_price:.2f} → ₹{trade.exit_price:.2f} | "
                f"P&L: ₹{pnl:+.2f} ({pnl_pct:+.2f}%) | held {hold_time}"
            )
            logger.info(f"Trade logged: {summary_line}")

            # Try LLM reflection; fall back to structured summary if unavailable
            reflection = self._generate_trade_reflection(trade, pnl, pnl_pct, hold_time, llm_client)
            journal_entry = (
                f"### {timestamp} — Exit: {trade.ticker}\n"
                f"{summary_line}\n\n"
                f"{reflection}\n"
            )

        with open(self.journal_path, "a") as f:
            f.write(f"\n{journal_entry}\n")

    def _generate_trade_reflection(
        self, trade: Trade, pnl: float, pnl_pct: float, hold_time: str, llm_client
    ) -> str:
        """Ask the LLM to reflect on a closed trade. Falls back to template if no client."""
        outcome_word = "profitable" if pnl > 0 else "a loss"

        prompt = f"""You are a trading journal assistant. Write a concise reflection on this closed trade.

## Trade Summary
- Ticker: {trade.ticker}
- Type: {trade.trade_type}
- Entry: ₹{trade.entry_price:.2f}  Exit: ₹{trade.exit_price:.2f}
- P&L: ₹{pnl:+.2f} ({pnl_pct:+.2f}%)
- Hold time: {hold_time}
- Entry thesis: {trade.reason or 'not recorded'}
- Exit reason: {trade.exit_reason or 'not recorded'}

Write 3–4 bullet points covering:
1. Did the original thesis play out? What actually drove the exit?
2. What went well or what went wrong?
3. One concrete thing to do differently next time (be specific, not generic)
4. Any pattern worth remembering for future {trade.ticker} or {trade.trade_type} trades

Keep it under 120 words. Be honest and specific. No fluff."""

        if llm_client is None:
            return self._fallback_trade_reflection(trade, pnl, pnl_pct)

        try:
            if self.config.llm_provider == "anthropic":
                response = llm_client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text.strip()
            else:
                model = (
                    self.config.openrouter_model
                    if self.config.llm_provider == "openrouter"
                    else self.config.openai_model
                )
                response = llm_client.chat.completions.create(
                    model=model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Trade reflection LLM call failed: {e}")
            return self._fallback_trade_reflection(trade, pnl, pnl_pct)

    def _fallback_trade_reflection(self, trade: Trade, pnl: float, pnl_pct: float) -> str:
        """Structured reflection without LLM."""
        outcome = "met target" if pnl > 0 else "hit stop / reversed"
        return (
            f"- **Outcome**: {outcome} ({pnl_pct:+.2f}%)\n"
            f"- **Original thesis**: {trade.reason or 'not recorded'}\n"
            f"- **Exit reason**: {trade.exit_reason or 'not recorded'}\n"
            f"- *LLM reflection unavailable — review manually*"
        )

    def get_performance_stats(self, days: int = 30) -> Dict[str, Any]:
        """Calculate aggregate performance stats for the review."""
        closed = self.portfolio.get_closed_trades(limit=100)

        if not closed:
            return {"total_trades": 0, "message": "No closed trades yet"}

        wins = [t for t in closed if (t.pnl or 0) > 0]
        losses = [t for t in closed if (t.pnl or 0) < 0]
        total_pnl = sum(t.pnl or 0 for t in closed)

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(t.pnl for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0,
            "best_trade": max((t.pnl or 0 for t in closed), default=0),
            "worst_trade": min((t.pnl or 0 for t in closed), default=0),
        }
