"""
Dashboard — FastAPI backend serving the trading agent's web dashboard.
Provides API endpoints + serves the frontend.
Supports multi-session switching via /api/sessions endpoints.

Run: uvicorn dashboard.app:app --reload --port 8000
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Add parent dir to path so we can import agent modules
sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from agent.config import AgentConfig
from agent.portfolio import Portfolio
from agent.market_data import MarketData
from agent.learner import Learner
from agent.risk_manager import RiskManager

# ── Session-aware component cache ────────────────────────────

_current_session_id: str = ""   # Empty = legacy mode
_components: Dict[str, Dict] = {}


def _get_components(session_id: str = None) -> Dict:
    """Get or lazily create components for a session."""
    global _current_session_id
    sid = session_id or _current_session_id

    if sid and sid not in _components:
        try:
            from agent.session import load_session
            from agent.market_presets import get_preset
            sc = load_session(sid)
            preset = get_preset(sc.market)
            config = AgentConfig.from_session(sc)
            portfolio = Portfolio(config.db_path, config.starting_capital)
            market_data = MarketData(config.watchlist, market_preset=preset)
            learner = Learner(config, portfolio)
            risk_manager = RiskManager(config, portfolio)
            _components[sid] = {
                "config": config, "session": sc, "preset": preset,
                "portfolio": portfolio, "market_data": market_data,
                "learner": learner, "risk_manager": risk_manager,
            }
        except Exception as e:
            # Fall back to legacy
            return _get_legacy_components()

    if sid and sid in _components:
        return _components[sid]

    # Legacy fallback
    return _get_legacy_components()


def _get_legacy_components() -> Dict:
    """Legacy single-session components (backward compat)."""
    if "__legacy__" not in _components:
        config = AgentConfig()
        portfolio = Portfolio(config.db_path, config.starting_capital)
        market_data = MarketData(config.watchlist)
        learner = Learner(config, portfolio)
        risk_manager = RiskManager(config, portfolio)
        _components["__legacy__"] = {
            "config": config, "session": None, "preset": None,
            "portfolio": portfolio, "market_data": market_data,
            "learner": learner, "risk_manager": risk_manager,
        }
    return _components["__legacy__"]


# Try to auto-detect sessions and use the first one
def _auto_detect_session():
    global _current_session_id
    try:
        from agent.session import list_sessions
        sessions = list_sessions()
        if sessions:
            _current_session_id = sessions[0]["session_id"]
    except Exception:
        pass


_auto_detect_session()

app = FastAPI(title="AlphaAgent API", version="2.0.0")

# ── CORS — allow Next.js frontend ────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "https://alphaagent.skdev.one",
    ],
    allow_origin_regex=r"https://.*\.(vercel\.app|skdev\.one)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models for request bodies ───────────────────────

MAX_RUNNING_AGENTS = 4  # Safety limit for VM memory


class CreateSessionRequest(BaseModel):
    session_id: str
    display_name: str = ""
    market: str = "nse"
    starting_capital: Optional[float] = None
    max_position_pct: float = 0.20
    max_open_positions: int = 5
    daily_loss_limit_pct: float = 0.02
    per_trade_loss_limit_pct: float = 0.01
    max_trade_amount: Optional[float] = None
    watchlist: Optional[List[str]] = None
    llm_provider: str = "openrouter"
    llm_model: str = "anthropic/claude-haiku-4-5"
    api_key_env: str = "OPENROUTER_API_KEY"
    intraday_interval_min: int = 15
    personality: str = ""
    import_learnings_from: Optional[str] = None  # session_id to copy learnings from


class AddDirectiveRequest(BaseModel):
    text: str
    type: str = "custom"  # "quick" or "custom"
    expiry: str = "today"  # "this_cycle", "today", "until_cleared"


class UpdateSessionRequest(BaseModel):
    display_name: Optional[str] = None
    starting_capital: Optional[float] = None
    max_position_pct: Optional[float] = None
    max_open_positions: Optional[int] = None
    daily_loss_limit_pct: Optional[float] = None
    per_trade_loss_limit_pct: Optional[float] = None
    max_trade_amount: Optional[float] = None
    watchlist: Optional[List[str]] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    api_key_env: Optional[str] = None
    intraday_interval_min: Optional[int] = None
    personality: Optional[str] = None


# ── Session Management Endpoints ─────────────────────────────


def _is_agent_running(session_id: str) -> dict:
    """Check if an agent process is running for a session.

    Uses /proc/<pid>/cmdline to verify the PID actually belongs to our agent,
    not a recycled PID from a different process after container restart.
    """
    from agent.session import SESSIONS_DIR
    lock_path = SESSIONS_DIR / session_id / "agent.lock"
    if not lock_path.exists():
        return {"running": False, "pid": None}
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)  # signal 0 = just check if alive

        # Verify this PID is actually our agent (not a recycled PID)
        try:
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_bytes().decode("utf-8", errors="replace")
                # Our agent processes have "run.py" and the session_id in their cmdline
                if "run.py" not in cmdline or session_id not in cmdline:
                    # PID exists but it's not our agent — stale lock
                    lock_path.unlink(missing_ok=True)
                    return {"running": False, "pid": None}
        except Exception:
            pass  # /proc not available (non-Linux) — fall back to PID-only check

        return {"running": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        return {"running": False, "pid": None}


def _auto_restart_agents():
    """On server startup, restart agents that had stale lock files.

    If a lock file exists but the PID is dead, it means the agent was running
    when the container restarted. Re-launch it automatically.
    """
    import logging
    logger = logging.getLogger("dashboard")
    try:
        from agent.session import SESSIONS_DIR
        if not SESSIONS_DIR.exists():
            return
        for session_dir in SESSIONS_DIR.iterdir():
            if not session_dir.is_dir():
                continue
            lock_path = session_dir / "agent.lock"
            config_path = session_dir / "config.yaml"
            if not lock_path.exists() or not config_path.exists():
                continue
            session_id = session_dir.name
            status = _is_agent_running(session_id)
            if status["running"]:
                continue  # Already alive, skip

            # Stale lock file — agent was running but died (container restart)
            logger.info(f"🔄 Auto-restarting agent for session '{session_id}' (stale lock detected)")
            lock_path.unlink(missing_ok=True)

            # Re-launch the agent
            python_exe = str(PROJECT_ROOT / ".venv" / "bin" / "python3")
            if not Path(python_exe).exists():
                python_exe = sys.executable

            env = os.environ.copy()
            env_file = PROJECT_ROOT / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        env[key.strip()] = val.strip().strip("'\"")

            proc = subprocess.Popen(
                [python_exe, str(PROJECT_ROOT / "run.py"), "--session", session_id, "--loop"],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(f"✅ Restarted agent '{session_id}' with PID {proc.pid}")
    except Exception as e:
        logger.error(f"Auto-restart failed: {e}")


# Run auto-restart on module load (when uvicorn starts)
_auto_restart_agents()


def _start_health_checker():
    """Background thread that periodically checks agent health and restarts dead ones."""
    import threading

    def _check_loop():
        import time as _time
        while True:
            _time.sleep(300)  # Check every 5 minutes
            try:
                _auto_restart_agents()
            except Exception:
                pass

    t = threading.Thread(target=_check_loop, daemon=True)
    t.start()


_start_health_checker()


@app.get("/api/sessions")
def get_sessions():
    """List all available sessions with running status and performance stats."""
    try:
        from agent.session import list_sessions, SESSIONS_DIR
        import sqlite3
        sessions = list_sessions()
        for s in sessions:
            status = _is_agent_running(s["session_id"])
            s["is_running"] = status["running"]
            s["pid"] = status["pid"]
            # Quick win rate from trades DB
            try:
                db_path = SESSIONS_DIR / s["session_id"] / "trades.db"
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    row = conn.execute(
                        "SELECT COUNT(*) as total, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins "
                        "FROM trades WHERE status = 'closed'"
                    ).fetchone()
                    conn.close()
                    total, wins = row[0] or 0, row[1] or 0
                    s["total_trades"] = total
                    s["win_rate"] = round((wins / total) * 100, 1) if total > 0 else None
                else:
                    s["total_trades"] = 0
                    s["win_rate"] = None
            except Exception:
                s["total_trades"] = 0
                s["win_rate"] = None
        return sessions
    except Exception:
        return []


@app.get("/api/sessions/current")
def get_current_session():
    """Get the currently active session ID."""
    return {"session_id": _current_session_id or "__legacy__"}


@app.post("/api/sessions/switch/{session_id}")
def switch_session(session_id: str):
    """Switch the active session."""
    global _current_session_id
    try:
        from agent.session import list_sessions
        valid_ids = {s["session_id"] for s in list_sessions()}
        if session_id not in valid_ids:
            raise HTTPException(404, f"Session '{session_id}' not found")
    except ImportError:
        raise HTTPException(500, "Session support not available (install pyyaml)")
    _current_session_id = session_id
    # Force reload on next request
    _components.pop(session_id, None)
    return {"session_id": session_id, "status": "switched"}


@app.post("/api/sessions")
def create_session(req: CreateSessionRequest):
    """Create a new trading session."""
    from agent.session import SessionConfig, save_session, SESSIONS_DIR

    # Check if session already exists
    if (SESSIONS_DIR / req.session_id / "config.yaml").exists():
        raise HTTPException(409, f"Session '{req.session_id}' already exists")

    sc = SessionConfig(
        session_id=req.session_id,
        display_name=req.display_name or req.session_id,
        market=req.market,
        starting_capital=req.starting_capital,
        max_position_pct=req.max_position_pct,
        max_open_positions=req.max_open_positions,
        daily_loss_limit_pct=req.daily_loss_limit_pct,
        per_trade_loss_limit_pct=req.per_trade_loss_limit_pct,
        max_trade_amount=req.max_trade_amount,
        watchlist=req.watchlist,
        llm_provider=req.llm_provider,
        llm_model=req.llm_model,
        api_key_env=req.api_key_env,
        intraday_interval_min=req.intraday_interval_min,
        personality=req.personality,
        created_at=datetime.now().isoformat(),
    )
    sc.resolve_defaults()
    save_session(sc)

    # Import learnings from another session if requested
    if req.import_learnings_from:
        try:
            source_dir = SESSIONS_DIR / req.import_learnings_from
            source_journal = source_dir / "journal.md"
            if source_journal.exists():
                source_content = source_journal.read_text()
                # Check if same market
                source_config = source_dir / "config.yaml"
                same_market = False
                if source_config.exists():
                    import yaml
                    with open(source_config) as f:
                        src_data = yaml.safe_load(f) or {}
                    same_market = src_data.get("market") == req.market

                target_journal = SESSIONS_DIR / req.session_id / "journal.md"
                if same_market:
                    # Same market: copy full journal (tickers are relevant)
                    target_journal.write_text(source_content)
                else:
                    # Cross-market: extract only the distilled rules block
                    rules_marker = "\n## 📌 Distilled Rules"
                    rules_end = "\n---"
                    rules_start = source_content.find(rules_marker)
                    if rules_start >= 0:
                        rules_end_pos = source_content.find(rules_end, rules_start + len(rules_marker))
                        if rules_end_pos >= 0:
                            rules_block = source_content[rules_start:rules_end_pos + len(rules_end)].strip()
                        else:
                            rules_block = source_content[rules_start:].strip()
                        # Write new journal with imported rules
                        target_journal.write_text(
                            "# 🧠 Trading Agent — Learning Journal\n\n"
                            "This file is automatically updated by the AI trading agent.\n\n"
                            "---\n\n"
                            f"{rules_block}\n\n---\n\n"
                            f"*Distilled rules imported from session '{req.import_learnings_from}' (cross-market transfer)*\n\n"
                        )
        except Exception as e:
            # Non-fatal — session still created, just without learnings
            import logging
            logging.getLogger("dashboard").warning(f"Failed to import learnings: {e}")

    return {"session_id": sc.session_id, "status": "created", "session_dir": str(sc.session_dir)}


@app.put("/api/sessions/{session_id}")
def update_session(session_id: str, req: UpdateSessionRequest):
    """Update an existing session config."""
    from agent.session import load_session, save_session

    try:
        sc = load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Session '{session_id}' not found")

    # Update only provided fields
    for field_name, value in req.model_dump(exclude_none=True).items():
        if hasattr(sc, field_name):
            setattr(sc, field_name, value)

    sc.resolve_defaults()
    save_session(sc)

    # Clear cached components so changes take effect
    _components.pop(session_id, None)

    # If model or interval changed and agent is running, auto-restart it
    restart_fields = {"llm_model", "llm_provider", "intraday_interval_min"}
    changed_fields = set(req.model_dump(exclude_none=True).keys())
    needs_restart = bool(restart_fields & changed_fields)

    restarted = False
    if needs_restart:
        status = _is_agent_running(session_id)
        if status["running"]:
            # Stop the agent
            try:
                os.kill(status["pid"], signal.SIGTERM)
                import time as _time
                _time.sleep(3)
                # Clear stale lock if process didn't clean up
                from agent.session import SESSIONS_DIR
                lock_path = SESSIONS_DIR / session_id / "agent.lock"
                if lock_path.exists():
                    try:
                        os.kill(status["pid"], 0)
                        os.kill(status["pid"], signal.SIGKILL)
                        _time.sleep(1)
                    except ProcessLookupError:
                        pass
                    lock_path.unlink(missing_ok=True)
            except Exception:
                pass

            # Restart with new config
            try:
                start_agent(session_id)
                restarted = True
            except Exception:
                pass

    return {"session_id": session_id, "status": "updated", "restarted": restarted}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete a session (only if agent is not running)."""
    import shutil
    from agent.session import SESSIONS_DIR

    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        raise HTTPException(404, f"Session '{session_id}' not found")

    # Don't delete if agent is running
    status = _is_agent_running(session_id)
    if status["running"]:
        raise HTTPException(409, f"Agent is running (PID {status['pid']}). Stop it first.")

    shutil.rmtree(session_dir)
    _components.pop(session_id, None)

    global _current_session_id
    if _current_session_id == session_id:
        _current_session_id = ""

    return {"session_id": session_id, "status": "deleted"}


# ── Agent Run Control ─────────────────────────────────────────

@app.get("/api/agent/status")
def get_all_agent_status():
    """Get running status for all sessions."""
    from agent.session import list_sessions
    sessions = list_sessions()
    result = {}
    for s in sessions:
        sid = s["session_id"]
        result[sid] = _is_agent_running(sid)
    return result


@app.get("/api/agent/status/{session_id}")
def get_agent_status(session_id: str):
    """Check if agent is running for a specific session."""
    return _is_agent_running(session_id)


@app.get("/api/agent/limits")
def get_agent_limits():
    """Get agent concurrency limits."""
    from agent.session import list_sessions
    all_sessions = list_sessions()
    running = sum(1 for s in all_sessions if _is_agent_running(s["session_id"])["running"])
    return {"max_agents": MAX_RUNNING_AGENTS, "running": running, "available": MAX_RUNNING_AGENTS - running}


@app.post("/api/agent/start/{session_id}")
def start_agent(session_id: str):
    """Start the agent loop for a session."""
    from agent.session import SESSIONS_DIR

    # Verify session exists
    if not (SESSIONS_DIR / session_id / "config.yaml").exists():
        raise HTTPException(404, f"Session '{session_id}' not found")

    # Check if already running
    status = _is_agent_running(session_id)
    if status["running"]:
        return {"status": "already_running", "pid": status["pid"]}

    # Check session limit
    from agent.session import list_sessions
    all_sessions = list_sessions()
    running_count = sum(1 for s in all_sessions if _is_agent_running(s["session_id"])["running"])
    if running_count >= MAX_RUNNING_AGENTS:
        raise HTTPException(
            429,
            f"Maximum {MAX_RUNNING_AGENTS} concurrent agents allowed. "
            f"Stop another agent before starting a new one."
        )

    # Find the right Python executable (prefer venv)
    python_exe = str(PROJECT_ROOT / ".venv" / "bin" / "python3")
    if not Path(python_exe).exists():
        python_exe = sys.executable

    run_script = str(PROJECT_ROOT / "run.py")

    # Load .env if it exists, merge with current env
    env = os.environ.copy()
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip("'\"")

    # Spawn the agent as a background process
    # stderr goes to session log so crashes are visible
    from agent.session import SESSIONS_DIR
    stderr_log = SESSIONS_DIR / session_id / "agent_stderr.log"
    stderr_file = open(stderr_log, "a")

    proc = subprocess.Popen(
        [python_exe, run_script, "--session", session_id, "--loop"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        start_new_session=True,  # Detach from parent
    )

    return {"status": "started", "pid": proc.pid, "session_id": session_id}


@app.post("/api/agent/stop/{session_id}")
def stop_agent(session_id: str):
    """Stop the agent for a session by sending SIGTERM."""
    status = _is_agent_running(session_id)
    if not status["running"]:
        return {"status": "not_running"}

    pid = status["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        return {"status": "stopped", "pid": pid}
    except ProcessLookupError:
        return {"status": "not_running"}
    except PermissionError:
        raise HTTPException(403, f"Cannot stop process {pid} — permission denied")


# ── Market Presets Endpoint ────────────────────────────────────

@app.get("/api/market-presets")
def get_market_presets():
    """Get available market presets for session creation form."""
    from agent.market_presets import MARKET_PRESETS
    result = {}
    for mid, preset in MARKET_PRESETS.items():
        result[mid] = {
            "market_id": preset.market_id,
            "display_name": preset.display_name,
            "currency": preset.currency,
            "currency_symbol": preset.currency_symbol,
            "is_24x7": preset.is_24x7,
            "default_starting_capital": preset.default_starting_capital,
            "default_watchlist_count": preset.default_watchlist_count or len(preset.default_watchlist),
            "timezone": preset.timezone,
            "ticker_suffix": preset.ticker_suffix,
            "trade_types": preset.trade_types,
            "short_allowed": preset.short_allowed,
        }
    return result


# ── API Routes (session-aware) ───────────────────────────────


@app.get("/api/portfolio")
def get_portfolio(session: str = None):
    """Get current portfolio summary."""
    c = _get_components(session)
    try:
        prices = c["market_data"].get_current_prices()
    except Exception:
        prices = {}
    return c["portfolio"].get_portfolio_summary(prices)


@app.get("/api/trades/open")
def get_open_trades(session: str = None):
    """Get all open positions."""
    c = _get_components(session)
    positions = c["portfolio"].get_open_positions()
    return [
        {
            "id": p.id, "ticker": p.ticker, "action": p.action,
            "direction": p.direction,
            "trade_type": p.trade_type, "quantity": p.quantity,
            "entry_price": p.entry_price, "entry_time": p.entry_time,
            "stop_price": p.stop_price, "target_price": p.target_price,
            "reason": p.reason,
        }
        for p in positions
    ]


@app.get("/api/trades/closed")
def get_closed_trades(limit: int = 30, session: str = None):
    """Get recent closed trades."""
    c = _get_components(session)
    trades = c["portfolio"].get_closed_trades(limit=limit)
    return [
        {
            "id": t.id, "ticker": t.ticker, "action": t.action,
            "direction": t.direction,
            "trade_type": t.trade_type, "quantity": t.quantity,
            "entry_price": t.entry_price, "entry_time": t.entry_time,
            "exit_price": t.exit_price, "exit_time": t.exit_time,
            "pnl": t.pnl, "status": t.status,
            "reason": t.reason, "exit_reason": t.exit_reason,
        }
        for t in trades
    ]


@app.get("/api/risk")
def get_risk_status(session: str = None):
    """Get current risk metrics."""
    c = _get_components(session)
    try:
        prices = c["market_data"].get_current_prices()
    except Exception:
        prices = {}
    return c["risk_manager"].get_risk_status(prices)


@app.get("/api/performance")
def get_performance(session: str = None):
    """Get aggregate performance stats."""
    c = _get_components(session)
    return c["learner"].get_performance_stats()


@app.get("/api/performance/detailed")
def get_detailed_performance(session: str = None):
    """Get detailed performance breakdown by category + distilled rules."""
    c = _get_components(session)
    learner = c["learner"]
    stats = learner.get_performance_stats()

    # Category breakdowns
    closed = c["portfolio"].get_closed_trades(limit=200)
    categories = {}
    for label, filter_fn in [
        ("long", lambda t: getattr(t, 'direction', 'long') == 'long'),
        ("short", lambda t: getattr(t, 'direction', 'long') == 'short'),
        ("intraday", lambda t: t.trade_type == 'intraday'),
        ("swing", lambda t: t.trade_type == 'swing'),
    ]:
        subset = [t for t in closed if filter_fn(t)]
        if not subset:
            continue
        wins = sum(1 for t in subset if (t.pnl or 0) > 0)
        categories[label] = {
            "total": len(subset),
            "wins": wins,
            "losses": len(subset) - wins,
            "win_rate": round(wins / len(subset) * 100, 1),
            "avg_pnl": round(sum(t.pnl or 0 for t in subset) / len(subset), 2),
            "total_pnl": round(sum(t.pnl or 0 for t in subset), 2),
        }

    # Exit type breakdown
    exit_types = {}
    for label, keyword in [
        ("stop_hit", "stop"),
        ("target_hit", "target"),
        ("forced_close", "forced"),
        ("manual", "manual"),
    ]:
        # Check both exit_type field and exit_reason text for backward compat
        subset = [t for t in closed if (
            getattr(t, 'exit_type', None) == label or
            keyword.lower() in (t.exit_reason or '').lower()
        )]
        if subset:
            exit_types[label] = {
                "total": len(subset),
                "avg_pnl": round(sum(t.pnl or 0 for t in subset) / len(subset), 2),
            }

    # Conviction breakdown (only for trades with conviction set)
    conviction_stats = {}
    for t in closed:
        conv = getattr(t, 'conviction', None)
        if conv is not None:
            if conv not in conviction_stats:
                conviction_stats[conv] = {"total": 0, "wins": 0, "total_pnl": 0}
            conviction_stats[conv]["total"] += 1
            if (t.pnl or 0) > 0:
                conviction_stats[conv]["wins"] += 1
            conviction_stats[conv]["total_pnl"] += (t.pnl or 0)
    for k, v in conviction_stats.items():
        v["win_rate"] = round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0
        v["total_pnl"] = round(v["total_pnl"], 2)

    # Extract distilled rules from journal
    rules = ""
    try:
        content = learner.journal_path.read_text()
        rules_marker = "\n## 📌 Distilled Rules"
        rules_end_marker = "\n---"
        rules_start = content.find(rules_marker)
        if rules_start >= 0:
            rules_end = content.find(rules_end_marker, rules_start + len(rules_marker))
            if rules_end >= 0:
                rules = content[rules_start:rules_end].strip()
    except Exception:
        pass

    return {
        "overall": stats,
        "by_direction": {k: v for k, v in categories.items() if k in ("long", "short")},
        "by_type": {k: v for k, v in categories.items() if k in ("intraday", "swing")},
        "by_exit": exit_types,
        "by_conviction": {str(k): v for k, v in sorted(conviction_stats.items())},
        "distilled_rules": rules,
    }


@app.get("/api/learnings")
def get_learnings(session: str = None):
    """Get the learning journal contents."""
    c = _get_components(session)
    return {"content": c["learner"].get_learnings(max_chars=10000)}


# ── Live Directives ──────────────────────────────────────────

@app.get("/api/directives")
def get_directives(session: str = None):
    """Get active directives for a session."""
    from agent.session import SESSIONS_DIR
    sid = session or _current_session_id
    if not sid:
        return {"directives": []}
    directive_path = SESSIONS_DIR / sid / "directive.json"
    if not directive_path.exists():
        return {"directives": []}
    try:
        data = json.loads(directive_path.read_text())
        directives = data.get("directives", [])
        now = datetime.now()
        active = []
        for d in directives:
            if d.get("expires_at"):
                exp = datetime.fromisoformat(d["expires_at"])
                if now > exp:
                    continue
            active.append(d)
        return {"directives": active}
    except Exception:
        return {"directives": []}


@app.post("/api/directives")
def add_directive(req: AddDirectiveRequest, session: str = None):
    """Add a new directive to a session."""
    from agent.session import SESSIONS_DIR
    import random, string, time as _time

    sid = session or _current_session_id
    if not sid:
        raise HTTPException(400, "No session specified")
    directive_path = SESSIONS_DIR / sid / "directive.json"

    if directive_path.exists():
        data = json.loads(directive_path.read_text())
    else:
        data = {"directives": []}

    now = datetime.now()
    if req.expiry == "today":
        expires_at = now.replace(hour=23, minute=59, second=59).isoformat()
    else:
        expires_at = None  # "this_cycle" handled by agent, "until_cleared" never expires

    rand_suffix = ''.join(random.choices(string.ascii_lowercase, k=3))
    directive = {
        "id": f"d_{int(_time.time())}_{rand_suffix}",
        "text": req.text.strip()[:500],
        "type": req.type,
        "expiry": req.expiry,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
    }
    data["directives"].append(directive)
    directive_path.write_text(json.dumps(data, indent=2))
    return {"status": "added", "directive": directive}


@app.delete("/api/directives/{directive_id}")
def delete_directive(directive_id: str, session: str = None):
    """Remove a specific directive."""
    from agent.session import SESSIONS_DIR

    sid = session or _current_session_id
    if not sid:
        raise HTTPException(400, "No session specified")
    directive_path = SESSIONS_DIR / sid / "directive.json"
    if not directive_path.exists():
        raise HTTPException(404, "No directives found")

    data = json.loads(directive_path.read_text())
    original_count = len(data["directives"])
    data["directives"] = [d for d in data["directives"] if d["id"] != directive_id]
    if len(data["directives"]) == original_count:
        raise HTTPException(404, f"Directive '{directive_id}' not found")
    directive_path.write_text(json.dumps(data, indent=2))
    return {"status": "deleted", "directive_id": directive_id}


# ── Daily Performance ────────────────────────────────────────

@app.get("/api/performance/daily")
def get_daily_performance(limit: int = 30, session: str = None):
    """Get per-day performance breakdown from snapshots + trades."""
    import sqlite3
    c = _get_components(session)
    db_path = c["config"].db_path
    starting_capital = c["config"].starting_capital

    try:
        with sqlite3.connect(db_path) as conn:
            snapshots = conn.execute(
                "SELECT date, cash, portfolio_value, total_value, daily_pnl, trades_taken, wins, losses "
                "FROM daily_snapshots ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()

            trade_days = conn.execute(
                """SELECT date(entry_time) as day,
                          COUNT(*) as trades_entered,
                          SUM(CASE WHEN status != 'open' AND pnl > 0 THEN 1 ELSE 0 END) as wins,
                          SUM(CASE WHEN status != 'open' AND pnl <= 0 THEN 1 ELSE 0 END) as losses,
                          ROUND(SUM(CASE WHEN status != 'open' THEN COALESCE(pnl, 0) ELSE 0 END), 2) as realized_pnl
                   FROM trades GROUP BY day ORDER BY day DESC LIMIT ?""", (limit,)
            ).fetchall()

        days = {}
        for row in snapshots:
            d = row[0]
            days[d] = {
                "date": d, "cash": row[1], "portfolio_value": row[2],
                "total_value": row[3], "daily_pnl": row[4],
                "trades_taken": row[5], "wins": row[6], "losses": row[7],
                "cumulative_return": round(row[3] - starting_capital, 2),
                "cumulative_return_pct": round(((row[3] - starting_capital) / starting_capital) * 100, 2),
            }

        for row in trade_days:
            d = row[0]
            if d not in days:
                days[d] = {
                    "date": d, "cash": None, "portfolio_value": None,
                    "total_value": None, "daily_pnl": row[4] or 0,
                    "trades_taken": row[1] or 0, "wins": row[2] or 0, "losses": row[3] or 0,
                    "cumulative_return": None, "cumulative_return_pct": None,
                }
            elif days[d]["trades_taken"] == 0 and (row[1] or 0) > 0:
                days[d]["trades_taken"] = row[1] or 0
                days[d]["wins"] = row[2] or 0
                days[d]["losses"] = row[3] or 0

        result = sorted(days.values(), key=lambda x: x["date"], reverse=True)
        return result[:limit]
    except Exception:
        return []


@app.get("/api/watchlist")
def get_watchlist(session: str = None):
    """Get watchlist with current data."""
    c = _get_components(session)
    try:
        summaries = c["market_data"].get_watchlist_summary()
        return summaries
    except Exception as e:
        return {"error": str(e), "watchlist": c["config"].watchlist}


@app.get("/api/snapshots")
def get_daily_snapshots(limit: int = 30, session: str = None):
    """Get daily portfolio snapshots for charting."""
    import sqlite3
    c = _get_components(session)
    try:
        with sqlite3.connect(c["config"].db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "date": r[0], "cash": r[1], "portfolio_value": r[2],
                "total_value": r[3], "daily_pnl": r[4],
                "trades_taken": r[5], "wins": r[6], "losses": r[7],
            }
            for r in rows
        ]
    except Exception:
        return []


@app.get("/api/logs")
def get_logs(lines: int = 150, session: str = None):
    """Get recent agent log lines."""
    c = _get_components(session)
    log_path = Path(c["config"].log_path)
    if not log_path.is_absolute():
        log_path = Path(__file__).parent.parent / log_path
    if not log_path.exists():
        return {"lines": []}
    try:
        all_lines = log_path.read_text().splitlines()
        return {"lines": all_lines[-lines:]}
    except Exception as e:
        return {"lines": [], "error": str(e)}


@app.get("/api/journal")
def get_journal(session: str = None):
    """Get full learning journal."""
    c = _get_components(session)
    journal_path = Path(c["config"].learnings_path)
    if not journal_path.is_absolute():
        journal_path = Path(__file__).parent.parent / journal_path
    if not journal_path.exists():
        return {"content": "No journal yet."}
    return {"content": journal_path.read_text()}


@app.get("/api/config")
def get_config(session: str = None):
    """Get agent configuration (non-sensitive) + market info for frontend."""
    c = _get_components(session)
    config = c["config"]
    preset = c.get("preset")
    sc = c.get("session")

    result = {
        "starting_capital": config.starting_capital,
        "currency": config.currency,
        "currency_symbol": config.currency_symbol,
        "max_position_pct": config.max_position_pct,
        "max_open_positions": config.max_open_positions,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
        "per_trade_loss_limit_pct": config.per_trade_loss_limit_pct,
        "max_trade_amount": config.max_trade_amount,
        "watchlist_count": len(config.watchlist),
        "llm_provider": config.llm_provider,
        "llm_model": getattr(config, f"{config.llm_provider}_model",
                             config.openrouter_model),
        "intraday_interval_min": config.intraday_interval_min,
        "market_open": config.market_open,
        "market_close": config.market_close,
    }

    # Add market-specific info for frontend
    if preset:
        result.update({
            "market_id": preset.market_id,
            "market_name": preset.display_name,
            "is_24x7": preset.is_24x7,
            "ticker_suffix": preset.ticker_suffix,
            "locale": preset.locale,
            "timezone": preset.timezone,
        })
    else:
        result.update({
            "market_id": "nse",
            "market_name": "Indian Stock Markets (NSE)",
            "is_24x7": False,
            "ticker_suffix": ".NS",
            "locale": "en-IN",
            "timezone": "Asia/Kolkata",
        })

    if sc:
        result["session_id"] = sc.session_id
        result["session_name"] = sc.display_name
        result["personality"] = sc.personality or ""

    return result


# ── Serve Frontend ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the main dashboard HTML."""
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return html_path.read_text()
    return "<h1>Dashboard HTML not found. Run from project root.</h1>"
