"""
Dashboard — FastAPI backend serving the trading agent's web dashboard.
Provides API endpoints + serves the frontend.

Run: uvicorn dashboard.app:app --reload --port 8000
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Add parent dir to path so we can import agent modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.config import AgentConfig
from agent.portfolio import Portfolio
from agent.market_data import MarketData
from agent.learner import Learner
from agent.risk_manager import RiskManager

# ── Initialize ───────────────────────────────────────────────

config = AgentConfig()
portfolio = Portfolio(config.db_path, config.starting_capital)
market_data = MarketData(config.watchlist)
learner = Learner(config, portfolio)
risk_manager = RiskManager(config, portfolio)

app = FastAPI(title="AI Trader Dashboard", version="1.0.0")

# ── API Routes ───────────────────────────────────────────────

@app.get("/api/portfolio")
def get_portfolio():
    """Get current portfolio summary."""
    try:
        prices = market_data.get_current_prices()
    except Exception:
        prices = {}
    return portfolio.get_portfolio_summary(prices)


@app.get("/api/trades/open")
def get_open_trades():
    """Get all open positions."""
    positions = portfolio.get_open_positions()
    return [
        {
            "id": p.id, "ticker": p.ticker, "action": p.action,
            "trade_type": p.trade_type, "quantity": p.quantity,
            "entry_price": p.entry_price, "entry_time": p.entry_time,
            "reason": p.reason,
        }
        for p in positions
    ]


@app.get("/api/trades/closed")
def get_closed_trades(limit: int = 30):
    """Get recent closed trades."""
    trades = portfolio.get_closed_trades(limit=limit)
    return [
        {
            "id": t.id, "ticker": t.ticker, "action": t.action,
            "trade_type": t.trade_type, "quantity": t.quantity,
            "entry_price": t.entry_price, "entry_time": t.entry_time,
            "exit_price": t.exit_price, "exit_time": t.exit_time,
            "pnl": t.pnl, "status": t.status,
            "reason": t.reason, "exit_reason": t.exit_reason,
        }
        for t in trades
    ]


@app.get("/api/risk")
def get_risk_status():
    """Get current risk metrics."""
    try:
        prices = market_data.get_current_prices()
    except Exception:
        prices = {}
    return risk_manager.get_risk_status(prices)


@app.get("/api/performance")
def get_performance():
    """Get aggregate performance stats."""
    return learner.get_performance_stats()


@app.get("/api/learnings")
def get_learnings():
    """Get the learning journal contents."""
    return {"content": learner.get_learnings(max_chars=10000)}


@app.get("/api/watchlist")
def get_watchlist():
    """Get watchlist with current data."""
    try:
        summaries = market_data.get_watchlist_summary()
        return summaries
    except Exception as e:
        return {"error": str(e), "watchlist": config.watchlist}


@app.get("/api/snapshots")
def get_daily_snapshots(limit: int = 30):
    """Get daily portfolio snapshots for charting."""
    import sqlite3
    try:
        with sqlite3.connect(config.db_path) as conn:
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
def get_logs(lines: int = 150):
    """Get recent agent log lines."""
    log_path = Path(__file__).parent.parent / config.log_path
    if not log_path.exists():
        return {"lines": []}
    try:
        all_lines = log_path.read_text().splitlines()
        return {"lines": all_lines[-lines:]}
    except Exception as e:
        return {"lines": [], "error": str(e)}


@app.get("/api/journal")
def get_journal():
    """Get full learning journal."""
    journal_path = Path(__file__).parent.parent / config.learnings_path
    if not journal_path.exists():
        return {"content": "No journal yet."}
    return {"content": journal_path.read_text()}


@app.get("/api/config")
def get_config():
    """Get agent configuration (non-sensitive)."""
    return {
        "starting_capital": config.starting_capital,
        "currency": config.currency,
        "max_position_pct": config.max_position_pct,
        "max_open_positions": config.max_open_positions,
        "daily_loss_limit_pct": config.daily_loss_limit_pct,
        "per_trade_loss_limit_pct": config.per_trade_loss_limit_pct,
        "max_trade_amount": config.max_trade_amount,
        "watchlist_count": len(config.watchlist),
        "llm_provider": config.llm_provider,
        "intraday_interval_min": config.intraday_interval_min,
        "market_open": config.market_open,
        "market_close": config.market_close,
    }


# ── Serve Frontend ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the main dashboard HTML."""
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return html_path.read_text()
    return "<h1>Dashboard HTML not found. Run from project root.</h1>"
