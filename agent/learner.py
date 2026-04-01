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

    def write_trade_log(self, trade: Trade, context: str = ""):
        """Log individual trade entry to journal and logger."""
        entry = (
            f"**{trade.action}** {trade.quantity}x {trade.ticker} "
            f"@ ₹{trade.entry_price:.2f}"
        )
        if trade.exit_price:
            entry += f" → ₹{trade.exit_price:.2f} (P&L: ₹{trade.pnl:.2f})"
        if trade.reason:
            entry += f"\n  *Reason: {trade.reason}*"
        if context:
            entry += f"\n  *Context: {context}*"

        logger.info(f"Trade logged: {entry}")

        # Append to journal so decisions are visible in real-time
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        journal_entry = f"### {timestamp} — Trade\n{entry}\n"
        with open(self.journal_path, "a") as f:
            f.write(f"\n{journal_entry}\n")

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
