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
        # If path is already absolute (session-based), use directly;
        # otherwise resolve relative to project root (backward compat)
        lp = Path(config.learnings_path)
        if lp.is_absolute():
            self.journal_path = lp
        else:
            _project_root = Path(__file__).resolve().parent.parent
            self.journal_path = (_project_root / config.learnings_path).resolve()
        self._ensure_journal()

    @property
    def _sym(self) -> str:
        """Currency symbol from config."""
        return self.config.currency_symbol

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

    def get_learnings(self, max_chars: int = 4000) -> str:
        """
        Read learnings to feed back into the LLM context.

        Strategy: always include the persistent rules block (## Distilled Rules section)
        at the top, then fill remaining space with the most recent trade entries.
        This ensures the agent always has its accumulated wisdom regardless of journal size.
        """
        content = self.journal_path.read_text()

        # Split out the distilled rules block if it exists
        rules_marker = "\n## 📌 Distilled Rules"
        rules_end_marker = "\n---"
        rules_block = ""
        rest = content

        rules_start = content.find(rules_marker)
        if rules_start >= 0:
            rules_end = content.find(rules_end_marker, rules_start + len(rules_marker))
            if rules_end >= 0:
                rules_block = content[rules_start:rules_end + len(rules_end_marker)].strip()
                # Remove the rules block from rest so we don't double-count
                rest = content[:rules_start] + content[rules_end + len(rules_end_marker):]

        # Budget: rules get up to half the space, recent entries fill the rest
        rules_budget = min(len(rules_block), max_chars // 2)
        entries_budget = max_chars - rules_budget

        # Take the most recent entries that fit in the budget
        if len(rest) > entries_budget:
            truncated = rest[-entries_budget:]
            # Start from a clean entry header
            for header in ["\n### ", "\n## "]:
                entry_start = truncated.find(header)
                if entry_start > 0:
                    truncated = truncated[entry_start:]
                    break
            recent_entries = f"[... earlier entries truncated ...]\n{truncated}"
        else:
            recent_entries = rest

        if rules_block:
            return f"{rules_block}\n\n---\n\n{recent_entries}"
        return recent_entries

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

        # Compute stats to ground the review in numbers, not just narrative
        stats = self.get_performance_stats()
        stats_by_type = self._get_stats_by_category()

        review_prompt = f"""Review today's trading activity and write a brief learning reflection.

## Today's Trades
```json
{json.dumps(trades_data, indent=2)}
```

## Portfolio Summary
```json
{json.dumps(summary, indent=2)}
```

## Quantitative Performance (all-time)
```
Overall:  {stats.get('total_trades', 0)} trades | Win rate: {stats.get('win_rate', 0)}% | Avg win: {self._sym}{stats.get('avg_win', 0)} | Avg loss: {self._sym}{stats.get('avg_loss', 0)}
{stats_by_type}
```

## Previous Learnings
{self.get_learnings(max_chars=1500)}

---

Write a concise reflection (3-5 bullet points) covering:
1. What worked well today and why — reference the quantitative stats above
2. What didn't work and what you'd do differently
3. Patterns you notice — cite specific win rates by category (long vs short, intraday vs swing)
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
- **Day P&L**: {self._sym}{total_pnl:.2f}
- **Portfolio value**: {self._sym}{summary['total_value']:.2f}
- *Note: LLM review unavailable, this is a stats-only summary*
"""

    def update_distilled_rules(self, llm_client) -> None:
        """
        Ask the LLM to synthesize ALL journal entries into a short persistent
        rules block (## 📌 Distilled Rules). This block is always included at
        the top of get_learnings() so no wisdom is lost to truncation.
        Called at end-of-day after generate_daily_review().
        """
        content = self.journal_path.read_text()

        # Strip existing rules block so we regenerate from scratch
        rules_marker = "\n## 📌 Distilled Rules"
        rules_start = content.find(rules_marker)
        rules_end_marker = "\n---"
        if rules_start >= 0:
            rules_end = content.find(rules_end_marker, rules_start + len(rules_marker))
            if rules_end >= 0:
                content_without_rules = content[:rules_start] + content[rules_end + len(rules_end_marker):]
            else:
                content_without_rules = content[:rules_start]
        else:
            content_without_rules = content

        # Don't burn tokens if journal is tiny
        if len(content_without_rules.strip()) < 500:
            return

        # Inject quantitative stats so rules are grounded in data
        stats = self.get_performance_stats()
        stats_by_type = self._get_stats_by_category()

        total_trades = stats.get('total_trades', 0)

        prompt = f"""You are a trading journal assistant. Read ALL the trade entries, reflections, AND quantitative stats below. Extract the most important, actionable trading rules.

## Quantitative Performance
```
Overall:  {total_trades} trades | Win rate: {stats.get('win_rate', 0)}% | Avg win: {self._sym}{stats.get('avg_win', 0)} | Avg loss: {self._sym}{stats.get('avg_loss', 0)}
{stats_by_type}
```

## Journal Entries
{content_without_rules[-8000:]}

---

Write a "## 📌 Distilled Rules" section (max 20 bullet points). It MUST follow these statistical-discipline guardrails, because we have only {total_trades} closed trades total and small samples lie:

**Sample-size rules (NON-NEGOTIABLE):**
- **N < 10 in a category → no verdict at all.** Do not call any setup "WORKS", "FAILS", "discontinue", or "avoid". Categories include: long, short, intraday, swing, any specific ticker/sector, any specific signal pattern.
- **10 ≤ N < 30 in a category → tentative observation only.** Use language like "early signal", "needs more data", "tentatively avoid until we have more samples". Never write absolute bans.
- **N ≥ 30 in a category → you may write a confident rule** ("Setup X: 22/30 wins, reliable").
- **A 0-win streak under 10 trades is noise, not evidence.** A losing streak is not a setup type — it's randomness until proven otherwise.

**Exploration mandate (ALWAYS include this rule verbatim):**
- "Exploration budget: even when a setup looks dominant, allocate at least 1 in every 4 trade ideas to alternative setups (different direction, different signal type, different ticker class) until each category has reached 30 closed trades. Confident bans require N≥30."

**What to write:**
- Statistically supported wins (N≥10): cite the win rate
- Statistically supported losses (N≥30): cite the loss rate
- Position sizing / stop / target observations (these can apply across categories with smaller N)
- The exploration mandate above
- "Categories under N=30: <list>. Keep sampling these — verdict premature."

Rules must be SPECIFIC and DATA-BACKED (e.g. "Long RSI<30 bounces: 8/11 wins (73% WR), avg +1.2%. Tentative — needs N≥30."). No "always avoid X" or "discontinue Y" from samples under 30.

Format as a bullet list under `## 📌 Distilled Rules`. No preamble, just the section."""

        try:
            if self.config.llm_provider == "anthropic":
                response = llm_client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                )
                rules_text = response.content[0].text.strip()
            else:
                model = (
                    self.config.openrouter_model
                    if self.config.llm_provider == "openrouter"
                    else self.config.openai_model
                )
                response = llm_client.chat.completions.create(
                    model=model,
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                )
                rules_text = response.choices[0].message.content.strip()

            # Ensure it starts with the right header
            if not rules_text.startswith("## 📌 Distilled Rules"):
                rules_text = "## 📌 Distilled Rules\n\n" + rules_text

            # Insert rules block right after the journal header (before first trade entry)
            header_end = content_without_rules.find("\n---\n")
            if header_end >= 0:
                insert_at = header_end + len("\n---\n")
            else:
                insert_at = len(content_without_rules)

            new_content = (
                content_without_rules[:insert_at]
                + f"\n{rules_text}\n\n---\n\n"
                + content_without_rules[insert_at:]
            )
            self.journal_path.write_text(new_content)
            logger.info("📌 Distilled rules block updated in journal")

        except Exception as e:
            logger.warning(f"Failed to update distilled rules: {e}")

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

        if trade.exit_price is None:
            # ── Entry log ─────────────────────────────────────────────
            # Dedup: skip if this trade_id was already logged as an entry
            if trade.id is not None:
                existing = self.journal_path.read_text()
                dedup_marker = f"trade_id={trade.id}]"
                if dedup_marker in existing:
                    logger.debug(f"Skipping duplicate entry log for trade {trade.id}")
                    return

            action_label = "SHORT" if trade.direction == "short" else "ENTRY"
            entry = (
                f"**{action_label}** {trade.quantity}x {trade.ticker} @ {self._sym}{trade.entry_price:.2f}"
                f"  [{trade.trade_type}]  [trade_id={trade.id}]\n"
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
                f"{outcome} | {self._sym}{trade.entry_price:.2f} → {self._sym}{trade.exit_price:.2f} | "
                f"P&L: {self._sym}{pnl:+.2f} ({pnl_pct:+.2f}%) | held {hold_time}"
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
- Entry: {self._sym}{trade.entry_price:.2f}  Exit: {self._sym}{trade.exit_price:.2f}
- P&L: {self._sym}{pnl:+.2f} ({pnl_pct:+.2f}%)
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

    def _get_stats_by_category(self) -> str:
        """Compute win rate breakdowns by direction and trade type for prompt injection."""
        closed = self.portfolio.get_closed_trades(limit=200)
        if not closed:
            return "No closed trades yet."

        lines = []
        for label, filter_fn in [
            ("Long", lambda t: getattr(t, 'direction', 'long') == 'long'),
            ("Short", lambda t: getattr(t, 'direction', 'long') == 'short'),
            ("Intraday", lambda t: t.trade_type == 'intraday'),
            ("Swing", lambda t: t.trade_type == 'swing'),
        ]:
            subset = [t for t in closed if filter_fn(t)]
            if not subset:
                continue
            wins = sum(1 for t in subset if (t.pnl or 0) > 0)
            wr = round(wins / len(subset) * 100, 1) if subset else 0
            avg_pnl = round(sum(t.pnl or 0 for t in subset) / len(subset), 2)
            lines.append(f"{label:10s}: {len(subset):3d} trades | WR: {wr}% | Avg P&L: {self._sym}{avg_pnl}")

        # Per-exit-type breakdown if exit_type is tracked
        for label, keyword in [
            ("Stop hits", "stop"),
            ("Target hits", "target"),
        ]:
            subset = [t for t in closed if keyword.lower() in (t.exit_reason or '').lower()]
            if subset:
                avg_pnl = round(sum(t.pnl or 0 for t in subset) / len(subset), 2)
                lines.append(f"{label:10s}: {len(subset):3d} trades | Avg P&L: {self._sym}{avg_pnl}")

        return "\n".join(lines) if lines else "No closed trades yet."

    def get_performance_stats(self, days: int = 30) -> Dict[str, Any]:
        """Calculate aggregate performance stats for the review."""
        closed = self.portfolio.get_closed_trades(limit=100)

        if not closed:
            return {"total_trades": 0, "message": "No closed trades yet"}

        wins = [t for t in closed if (t.pnl or 0) > 0]
        losses = [t for t in closed if (t.pnl or 0) < 0]
        breakevens = [t for t in closed if (t.pnl or 0) == 0]
        total_pnl = sum(t.pnl or 0 for t in closed)
        # Win rate excludes breakevens from denominator (industry standard)
        decisive = len(wins) + len(losses)

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate": round(len(wins) / decisive * 100, 1) if decisive else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(t.pnl for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0,
            "best_trade": max((t.pnl or 0 for t in closed), default=0),
            "worst_trade": min((t.pnl or 0 for t in closed), default=0),
        }
