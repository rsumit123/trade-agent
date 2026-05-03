"""
Configuration for the AI Trading Agent.
All tunable parameters live here — risk limits, watchlist, API settings, etc.

For multi-session support, use AgentConfig.from_session(session_config) to build
a config from a session YAML file.  Direct AgentConfig() still works for backward
compatibility (defaults to NSE with the old paths).
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Any


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

    # ── Watchlist ───────────────────────────────────────────
    # Default: NSE watchlist from market preset (backward compat)
    watchlist: List[str] = field(default_factory=lambda: _default_nse_watchlist())

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
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── Session Integration (set by from_session()) ──────────
    _api_key_override: str = ""
    _market_preset: Any = None       # MarketPreset reference (avoid circular import in type hint)
    _session_config: Any = None      # SessionConfig reference

    @property
    def api_key(self) -> str:
        # If set by from_session(), _api_key_override contains the env var name
        # or a literal key. Try as env var first, then as literal.
        if self._api_key_override:
            # Try as env var name first
            val = os.environ.get(self._api_key_override, "")
            if val:
                return val
            # If it looks like a key (long, not all-caps), use it directly
            if len(self._api_key_override) > 20 and not self._api_key_override.isupper():
                return self._api_key_override
            raise ValueError(
                f"API key not found. Set the '{self._api_key_override}' environment variable."
            )
        # Legacy: look up env var based on provider
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

    @property
    def currency_symbol(self) -> str:
        """Dynamic currency symbol from market preset, or default based on currency."""
        if self._market_preset:
            return self._market_preset.currency_symbol
        return "₹" if self.currency == "INR" else "$"

    # ── Paths ────────────────────────────────────────────────
    db_path: str = "data/trades.db"
    learnings_path: str = "learnings/journal.md"
    log_path: str = "logs/agent.log"

    # ── Web Research ─────────────────────────────────────────
    max_news_results: int = 5
    news_sources: List[str] = field(default_factory=lambda: [
        "moneycontrol", "economictimes", "livemint", "reuters", "bloomberg"
    ])

    # ── Factory: build from session config ───────────────────

    @classmethod
    def from_session(cls, sc) -> "AgentConfig":
        """
        Build an AgentConfig from a SessionConfig + its market preset.
        All paths, keys, and market-specific settings come from the session.
        """
        from .market_presets import get_preset
        preset = get_preset(sc.market)

        # Map the single llm_model to the right provider-specific field
        model = sc.llm_model
        anthropic_model = model if sc.llm_provider == "anthropic" else "claude-sonnet-4-5-20250929"
        openai_model = model if sc.llm_provider == "openai" else "gpt-4o"
        openrouter_model = model if sc.llm_provider == "openrouter" else "google/gemini-2.5-flash"

        return cls(
            starting_capital=sc.starting_capital,
            currency=preset.currency,
            max_position_pct=sc.max_position_pct,
            max_open_positions=sc.max_open_positions,
            daily_loss_limit_pct=sc.daily_loss_limit_pct,
            per_trade_loss_limit_pct=sc.per_trade_loss_limit_pct,
            max_trade_amount=sc.max_trade_amount,
            watchlist=sc.watchlist,
            market_open=preset.market_open or "00:00",
            market_close=preset.market_close or "23:59",
            intraday_interval_min=sc.intraday_interval_min,
            timezone=preset.timezone,
            llm_provider=sc.llm_provider,
            anthropic_model=anthropic_model,
            openai_model=openai_model,
            openrouter_model=openrouter_model,
            _api_key_override=sc.api_key_env,  # env var name or literal key — resolved lazily
            _market_preset=preset,
            _session_config=sc,
            db_path=sc.db_path,
            learnings_path=sc.journal_path,
            log_path=sc.log_path,
            news_sources=preset.news_sources,
        )


def _default_nse_watchlist() -> List[str]:
    """Load the NSE watchlist from market_presets (avoids duplicating 111 tickers)."""
    try:
        from .market_presets import NSE_PRESET
        return list(NSE_PRESET.default_watchlist)
    except ImportError:
        # Fallback: shouldn't happen, but keep things working
        return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]
