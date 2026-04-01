"""
Paper Trading Engine — manages virtual portfolio, executes trades, tracks P&L.
All state persisted in SQLite.
"""

import sqlite3
import json
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum


class TradeType(str, Enum):
    INTRADAY = "intraday"
    SWING = "swing"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    STOPPED_OUT = "stopped_out"


@dataclass
class Trade:
    id: Optional[int]
    ticker: str
    action: str  # BUY or SELL
    trade_type: str  # intraday or swing
    quantity: int
    entry_price: float
    entry_time: str
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    status: str = "open"
    pnl: Optional[float] = None
    reason: str = ""
    exit_reason: str = ""


class Portfolio:
    """Paper trading portfolio backed by SQLite."""

    def __init__(self, db_path: str, starting_capital: float):
        self.db_path = db_path
        self.starting_capital = starting_capital
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    starting_capital REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_price REAL,
                    exit_time TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    pnl REAL,
                    reason TEXT DEFAULT '',
                    exit_reason TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    date TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    portfolio_value REAL NOT NULL,
                    total_value REAL NOT NULL,
                    daily_pnl REAL NOT NULL,
                    trades_taken INTEGER NOT NULL,
                    wins INTEGER NOT NULL,
                    losses INTEGER NOT NULL
                );
            """)

            # Initialize account if not exists
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO account (id, cash, starting_capital, created_at) VALUES (1, ?, ?, ?)",
                    (starting_capital, starting_capital, datetime.now().isoformat())
                )

    # ── Account State ────────────────────────────────────────

    def get_cash(self) -> float:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()[0]

    def _update_cash(self, conn, amount: float):
        conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount,))

    # ── Trade Execution ──────────────────────────────────────

    def execute_buy(self, ticker: str, quantity: int, price: float,
                    trade_type: str, reason: str = "") -> Trade:
        """Execute a paper BUY order."""
        cost = quantity * price
        cash = self.get_cash()
        if cost > cash:
            raise ValueError(f"Insufficient funds: need ₹{cost:.2f}, have ₹{cash:.2f}")

        with sqlite3.connect(self.db_path) as conn:
            self._update_cash(conn, -cost)
            cursor = conn.execute(
                """INSERT INTO trades (ticker, action, trade_type, quantity,
                   entry_price, entry_time, status, reason)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
                (ticker, "BUY", trade_type, quantity, price,
                 datetime.now().isoformat(), reason)
            )
            trade_id = cursor.lastrowid

        return Trade(
            id=trade_id, ticker=ticker, action="BUY", trade_type=trade_type,
            quantity=quantity, entry_price=price,
            entry_time=datetime.now().isoformat(), reason=reason
        )

    def execute_sell(self, trade_id: int, price: float, reason: str = "") -> Trade:
        """Close an open position by selling."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND status = 'open'", (trade_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"No open trade with id {trade_id}")

            trade = self._row_to_trade(row)
            pnl = (price - trade.entry_price) * trade.quantity
            proceeds = trade.quantity * price

            self._update_cash(conn, proceeds)
            conn.execute(
                """UPDATE trades SET exit_price = ?, exit_time = ?,
                   status = 'closed', pnl = ?, exit_reason = ? WHERE id = ?""",
                (price, datetime.now().isoformat(), pnl, reason, trade_id)
            )

        trade.exit_price = price
        trade.exit_time = datetime.now().isoformat()
        trade.status = "closed"
        trade.pnl = pnl
        trade.exit_reason = reason
        return trade

    # ── Queries ───────────────────────────────────────────────

    def get_open_positions(self) -> List[Trade]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time DESC"
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_closed_trades(self, limit: int = 20) -> List[Trade]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status != 'open' ORDER BY exit_time DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_today_trades(self) -> List[Trade]:
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_today_pnl(self) -> float:
        trades = self.get_today_trades()
        return sum(t.pnl or 0.0 for t in trades if t.status != "open")

    def get_portfolio_summary(self, current_prices: Dict[str, float] = None) -> Dict[str, Any]:
        """Full portfolio snapshot for LLM context."""
        cash = self.get_cash()
        open_positions = self.get_open_positions()

        holdings_value = 0.0
        holdings = []
        for pos in open_positions:
            current = current_prices.get(pos.ticker, pos.entry_price) if current_prices else pos.entry_price
            unrealized = (current - pos.entry_price) * pos.quantity
            market_val = current * pos.quantity
            holdings_value += market_val
            holdings.append({
                "trade_id": pos.id,
                "ticker": pos.ticker,
                "qty": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": current,
                "unrealized_pnl": round(unrealized, 2),
                "market_value": round(market_val, 2),
                "trade_type": pos.trade_type,
                "held_since": pos.entry_time,
                "reason": pos.reason,
            })

        total_value = cash + holdings_value
        total_return = total_value - self.starting_capital
        today_pnl = self.get_today_pnl()

        return {
            "cash": round(cash, 2),
            "holdings_value": round(holdings_value, 2),
            "total_value": round(total_value, 2),
            "starting_capital": self.starting_capital,
            "total_return": round(total_return, 2),
            "total_return_pct": round((total_return / self.starting_capital) * 100, 2),
            "today_pnl": round(today_pnl, 2),
            "open_positions": len(open_positions),
            "holdings": holdings,
        }

    def save_daily_snapshot(self, current_prices: Dict[str, float]):
        """Save end-of-day snapshot."""
        summary = self.get_portfolio_summary(current_prices)
        today_trades = self.get_today_trades()
        closed_today = [t for t in today_trades if t.status != "open"]
        wins = sum(1 for t in closed_today if (t.pnl or 0) > 0)
        losses = sum(1 for t in closed_today if (t.pnl or 0) < 0)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_snapshots
                   (date, cash, portfolio_value, total_value, daily_pnl, trades_taken, wins, losses)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (date.today().isoformat(), summary["cash"], summary["holdings_value"],
                 summary["total_value"], summary["today_pnl"], len(today_trades), wins, losses)
            )

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _row_to_trade(row) -> Trade:
        return Trade(
            id=row[0], ticker=row[1], action=row[2], trade_type=row[3],
            quantity=row[4], entry_price=row[5], entry_time=row[6],
            exit_price=row[7], exit_time=row[8], status=row[9],
            pnl=row[10], reason=row[11], exit_reason=row[12]
        )
