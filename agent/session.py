"""
Session management — load, save, list, and create trading sessions.

Each session is an isolated trading environment with its own:
  - config.yaml  (settings)
  - trades.db    (portfolio & trade history)
  - journal.md   (learning journal)
  - agent.log    (activity log)
  - agent.lock   (PID lock)

Sessions live under  <project_root>/sessions/<session_id>/
"""

import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

try:
    import yaml
except ImportError:
    yaml = None  # Handled in load/save with clear error

from .market_presets import get_preset, MARKET_PRESETS

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


@dataclass
class SessionConfig:
    """Fully resolved configuration for one trading session."""

    # ── Identity ────────────────────────────────────────────
    session_id: str = ""
    display_name: str = ""
    market: str = "nse"                         # key into MARKET_PRESETS

    # ── Capital & Risk ──────────────────────────────────────
    starting_capital: Optional[float] = None    # None → use preset default
    max_position_pct: float = 0.20
    max_open_positions: int = 5
    daily_loss_limit_pct: float = 0.02
    per_trade_loss_limit_pct: float = 0.01
    max_trade_amount: Optional[float] = None    # None → 20% of capital

    # ── Watchlist ───────────────────────────────────────────
    watchlist: Optional[List[str]] = None       # None → use preset default

    # ── LLM Configuration ───────────────────────────────────
    llm_provider: str = "openrouter"
    llm_model: str = "google/gemini-2.5-flash"
    api_key_env: str = "OPENROUTER_API_KEY"     # env var name OR literal key
    api_key_encrypted: str = ""                 # Fernet-encrypted user-provided key (preferred over api_key_env)

    # ── Schedule ────────────────────────────────────────────
    intraday_interval_min: int = 15

    # ── Personality ─────────────────────────────────────────
    personality: str = ""                        # custom instructions for LLM

    # ── Data Source ────────────────────────────────────────
    data_source: str = "yfinance"                # "yfinance" or "kite"

    # ── Backtest ──────────────────────────────────────────
    backtest_mode: bool = False                  # True = backtest before live
    backtest_start_date: Optional[str] = None    # "YYYY-MM-DD"
    backtest_end_date: Optional[str] = None      # "YYYY-MM-DD"
    backtest_status: str = ""                    # "running", "completed", "failed", ""
    live_started_at: str = ""                    # ISO timestamp when Go-Live flipped backtest_mode off

    # ── Comparison ────────────────────────────────────────
    parent_session: str = ""                     # if non-empty, this is a child of parent_session

    # ── Ownership ─────────────────────────────────────────
    user_email: str = ""                         # owner; "" → admin/legacy session
    tier: str = "free"                           # "free" | "admin" — admin = unlimited runtime

    # ── Runtime accounting ────────────────────────────────
    # Set when the agent starts; cleared when it stops. Used to bill runtime
    # against the user's free-tier quota.
    started_at: str = ""                         # ISO-8601, "" when not running

    # ── Metadata ────────────────────────────────────────────
    created_at: str = ""

    # ── Resolved Paths (computed, not stored in YAML) ───────

    @property
    def session_dir(self) -> Path:
        return SESSIONS_DIR / self.session_id

    @property
    def db_path(self) -> str:
        return str(self.session_dir / "trades.db")

    @property
    def journal_path(self) -> str:
        return str(self.session_dir / "journal.md")

    @property
    def log_path(self) -> str:
        return str(self.session_dir / "agent.log")

    @property
    def lock_path(self) -> Path:
        return self.session_dir / "agent.lock"

    @property
    def api_key(self) -> str:
        """Resolve API key. Priority: encrypted user key > env var > literal."""
        if self.api_key_encrypted:
            try:
                from .secrets_store import decrypt
                v = decrypt(self.api_key_encrypted)
                if v:
                    return v
            except Exception:
                pass
        val = os.environ.get(self.api_key_env, "")
        if val:
            return val
        # Maybe api_key_env IS the key itself (for convenience)
        if len(self.api_key_env) > 20 and not self.api_key_env.isupper():
            return self.api_key_env
        raise ValueError(
            f"API key not found. Set the '{self.api_key_env}' environment variable."
        )

    def resolve_defaults(self):
        """Fill in None fields from the market preset."""
        preset = get_preset(self.market)
        if self.starting_capital is None:
            self.starting_capital = preset.default_starting_capital
        if self.watchlist is None:
            self.watchlist = list(preset.default_watchlist)
        if self.max_trade_amount is None:
            self.max_trade_amount = self.starting_capital * 0.20


# ── Session Operations ─────────────────────────────────────────────────


def _require_yaml():
    if yaml is None:
        raise ImportError(
            "pyyaml is required for session support. "
            "Install it: pip install pyyaml"
        )


# Fields that should be written to config.yaml
_YAML_FIELDS = {
    "session_id", "display_name", "market",
    "starting_capital", "max_position_pct", "max_open_positions",
    "daily_loss_limit_pct", "per_trade_loss_limit_pct", "max_trade_amount",
    "watchlist", "llm_provider", "llm_model", "api_key_env",
    "intraday_interval_min", "personality", "created_at",
    "data_source",
    "backtest_mode", "backtest_start_date", "backtest_end_date", "backtest_status", "live_started_at",
    "parent_session", "user_email", "api_key_encrypted",
    "tier", "started_at",
}


def load_session(session_id: str) -> SessionConfig:
    """Load a session config from disk and resolve defaults."""
    _require_yaml()
    config_path = SESSIONS_DIR / session_id / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Session '{session_id}' not found at {config_path}"
        )
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    # Only pass known fields to avoid errors on extra YAML keys
    known = {k for k in SessionConfig.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known}
    sc = SessionConfig(**filtered)
    sc.resolve_defaults()
    return sc


def list_sessions() -> List[Dict]:
    """List all available sessions (reads config.yaml from each session dir)."""
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        config_path = d / "config.yaml"
        if not config_path.exists():
            continue
        try:
            _require_yaml()
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            preset = get_preset(data.get("market", "nse"))
            sessions.append({
                "session_id": data.get("session_id", d.name),
                "display_name": data.get("display_name", d.name),
                "market": data.get("market", "unknown"),
                "currency_symbol": preset.currency_symbol,
                "starting_capital": data.get("starting_capital", preset.default_starting_capital),
                "llm_provider": data.get("llm_provider", "openrouter"),
                "llm_model": data.get("llm_model", ""),
                "backtest_mode": data.get("backtest_mode", False),
                "backtest_status": data.get("backtest_status", ""),
                "parent_session": data.get("parent_session", ""),
                "user_email": data.get("user_email", ""),
                "tier": data.get("tier", "free"),
                "started_at": data.get("started_at", ""),
            })
        except Exception:
            # Skip broken sessions
            sessions.append({
                "session_id": d.name,
                "display_name": f"{d.name} (error reading config)",
                "market": "unknown",
                "currency_symbol": "?",
                "starting_capital": 0,
            })
    return sessions


def save_session(sc: SessionConfig):
    """Write session config to disk, creating the directory structure."""
    _require_yaml()
    sc.session_dir.mkdir(parents=True, exist_ok=True)
    config_path = sc.session_dir / "config.yaml"

    # Only write user-facing YAML fields
    data = {}
    full = asdict(sc)
    for key in _YAML_FIELDS:
        if key in full:
            val = full[key]
            # Don't write None/empty optional fields
            if val is not None and val != "":
                data[key] = val

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)
