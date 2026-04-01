"""
Configuration for the AI Trading Agent.
All tunable parameters live here — risk limits, watchlist, API settings, etc.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class AgentConfig:
    # ── Capital & Portfolio ──────────────────────────────────
    starting_capital: float = 10_00_000.0  # ₹10 Lakhs
    currency: str = "INR"

    # ── Risk Limits (HARD — these override LLM decisions) ───
    max_position_pct: float = 0.20        # Max 20% of portfolio in one stock
    max_open_positions: int = 5           # Max concurrent positions
    daily_loss_limit_pct: float = 0.02    # Stop trading if down 2% in a day
    per_trade_loss_limit_pct: float = 0.01  # Stop-loss per trade: 1%
    max_trade_amount: float = 2_00_000.0  # Max ₹2L per trade

    # ── Watchlist (NSE symbols) ──────────────────────────────
    watchlist: List[str] = field(default_factory=lambda: [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
        "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "TITAN.NS",
        "SUNPHARMA.NS", "WIPRO.NS", "ULTRACEMCO.NS", "NESTLEIND.NS", "TATAMOTORS.NS",
    ])

    # ── Trading Schedule ─────────────────────────────────────
    market_open: str = "09:15"       # IST
    market_close: str = "15:30"      # IST
    intraday_interval_min: int = 15  # Check every 15 minutes for intraday
    swing_check_times: List[str] = field(default_factory=lambda: [
        "09:30", "12:00", "15:00"    # Check 3x/day for swing trades
    ])
    timezone: str = "Asia/Kolkata"

    # ── LLM Configuration ────────────────────────────────────
    llm_provider: str = "openrouter"  # "anthropic", "openai", or "openrouter"
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    openai_model: str = "gpt-4o"
    openrouter_model: str = "anthropic/claude-sonnet-4-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    @property
    def api_key(self) -> str:
        key_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_var = key_map.get(self.llm_provider, "OPENROUTER_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise ValueError(f"Set {env_var} env var")
        return key

    # ── Paths ────────────────────────────────────────────────
    db_path: str = "data/trades.db"
    learnings_path: str = "learnings/journal.md"
    log_path: str = "logs/agent.log"

    # ── Web Research ─────────────────────────────────────────
    max_news_results: int = 5
    news_sources: List[str] = field(default_factory=lambda: [
        "moneycontrol", "economictimes", "livemint", "reuters", "bloomberg"
    ])
