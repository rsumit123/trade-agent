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
    stop_price: Optional[float] = None    # ATR-derived stop level
    target_price: Optional[float] = None  # ATR-derived target level
    direction: str = "long"               # "long" or "short"
    conviction: Optional[int] = None      # 1-5 scale, set by LLM at entry
    exit_type: Optional[str] = None       # stop_hit, target_hit, manual, forced_close
    llm_model: Optional[str] = None       # model that made this trade decision


class Portfolio:
    """Paper trading portfolio backed by SQLite."""

    def __init__(self, db_path: str, starting_capital: float):
        self.db_path = db_path
        self.starting_capital = starting_capital
        # Optional clock override — set by backtest engine to use simulated
        # time for entry_time/exit_time so hold durations are meaningful
        self._clock = None  # callable returning datetime, or None for real time
        self._init_db()

    def _now_iso(self) -> str:
        """Return current timestamp as ISO string, respecting _clock override."""
        if self._clock is not None:
            try:
                return self._clock().isoformat()
            except Exception:
                pass
        return datetime.now().isoformat()

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

            # Schema migration: add columns if not present
            # (idempotent — silently ignored if columns already exist)
            for col, col_def in [
                ("stop_price", "REAL"),
                ("target_price", "REAL"),
                ("direction", "TEXT DEFAULT 'long'"),
                ("conviction", "INTEGER"),
                ("exit_type", "TEXT"),
                ("llm_model", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_def}")
                except Exception:
                    pass  # column already exists

            # Initialize account if not exists
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO account (id, cash, starting_capital, created_at) VALUES (1, ?, ?, ?)",
                    (self.starting_capital, self.starting_capital, datetime.now().isoformat())
                )

    # ── Account State ────────────────────────────────────────

    def get_cash(self) -> float:
        """
        Returns truly available cash — raw DB cash minus proceeds from open short positions
        (those proceeds are a liability that will be paid back on cover).
        """
        with sqlite3.connect(self.db_path) as conn:
            raw_cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()[0]
            # Subtract short sale proceeds sitting in cash (they're not spendable equity)
            short_proceeds = conn.execute(
                "SELECT COALESCE(SUM(quantity * entry_price), 0) "
                "FROM trades WHERE status='open' AND direction='short'"
            ).fetchone()[0]
        return raw_cash - short_proceeds

    def get_cash_raw(self) -> float:
        """Raw DB cash including short proceeds (used internally for accounting)."""
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()[0]

    def _update_cash(self, conn, amount: float):
        conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount,))

    # ── Trade Execution ──────────────────────────────────────

    def execute_buy(self, ticker: str, quantity: int, price: float,
                    trade_type: str, reason: str = "",
                    stop_price: float = None,
                    target_price: float = None,
                    conviction: int = None,
                    llm_model: str = None) -> Trade:
        """Execute a paper BUY order."""
        cost = quantity * price
        cash = self.get_cash()
        if cost > cash:
            raise ValueError(f"Insufficient funds: need ₹{cost:.2f}, have ₹{cash:.2f}")

        entry_time = self._now_iso()
        with sqlite3.connect(self.db_path) as conn:
            self._update_cash(conn, -cost)
            cursor = conn.execute(
                """INSERT INTO trades (ticker, action, trade_type, quantity,
                   entry_price, entry_time, status, reason, stop_price, target_price, conviction, llm_model)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
                (ticker, "BUY", trade_type, quantity, price,
                 entry_time, reason, stop_price, target_price, conviction, llm_model)
            )
            trade_id = cursor.lastrowid

        return Trade(
            id=trade_id, ticker=ticker, action="BUY", trade_type=trade_type,
            quantity=quantity, entry_price=price, entry_time=entry_time,
            reason=reason, stop_price=stop_price, target_price=target_price,
            conviction=conviction, llm_model=llm_model,
        )

    def execute_sell(self, trade_id: int, price: float, reason: str = "",
                     exit_type: str = None) -> Trade:
        """Close an open position by selling."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND status = 'open'", (trade_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"No open trade with id {trade_id}")

            trade = self._row_to_trade(row)
            pnl = round((price - trade.entry_price) * trade.quantity, 2)
            proceeds = trade.quantity * price

            now = self._now_iso()
            self._update_cash(conn, proceeds)
            conn.execute(
                """UPDATE trades SET exit_price = ?, exit_time = ?,
                   status = 'closed', pnl = ?, exit_reason = ?, exit_type = ? WHERE id = ?""",
                (price, now, pnl, reason, exit_type or "manual", trade_id)
            )

        trade.exit_price = price
        trade.exit_time = now
        trade.status = "closed"
        trade.pnl = pnl
        trade.exit_reason = reason
        trade.exit_type = exit_type or "manual"
        return trade

    def execute_short(self, ticker: str, quantity: int, price: float,
                      reason: str = "",
                      stop_price: float = None,
                      target_price: float = None,
                      conviction: int = None,
                      llm_model: str = None) -> Trade:
        """
        Open a paper SHORT position (sell first, cover later).
        Intraday only — must be covered by EOD.
        In paper trading, shorting doesn't require cash upfront; we credit the
        proceeds so the portfolio value stays consistent.
        """
        proceeds = quantity * price
        entry_time = self._now_iso()
        with sqlite3.connect(self.db_path) as conn:
            # Credit proceeds from the short sale
            self._update_cash(conn, proceeds)
            cursor = conn.execute(
                """INSERT INTO trades (ticker, action, trade_type, quantity,
                   entry_price, entry_time, status, reason, stop_price, target_price, direction, conviction, llm_model)
                   VALUES (?, 'SHORT', 'intraday', ?, ?, ?, 'open', ?, ?, ?, 'short', ?, ?)""",
                (ticker, quantity, price, entry_time, reason, stop_price, target_price, conviction, llm_model)
            )
            trade_id = cursor.lastrowid

        return Trade(
            id=trade_id, ticker=ticker, action="SHORT", trade_type="intraday",
            quantity=quantity, entry_price=price, entry_time=entry_time,
            reason=reason, stop_price=stop_price, target_price=target_price,
            direction="short", conviction=conviction, llm_model=llm_model,
        )

    def execute_cover(self, trade_id: int, price: float, reason: str = "",
                      exit_type: str = None) -> Trade:
        """
        Close a SHORT position by buying to cover.
        P&L = (entry_price - cover_price) × quantity  (positive when price fell)
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND status = 'open' AND action = 'SHORT'",
                (trade_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"No open SHORT trade with id {trade_id}")

            trade = self._row_to_trade(row)
            # P&L: shorted at entry_price, covering at price
            pnl = round((trade.entry_price - price) * trade.quantity, 2)
            # Debit the cost to buy back shares
            cost = trade.quantity * price
            now = self._now_iso()
            self._update_cash(conn, -cost)
            conn.execute(
                """UPDATE trades SET exit_price = ?, exit_time = ?,
                   status = 'closed', pnl = ?, exit_reason = ?, exit_type = ? WHERE id = ?""",
                (price, now, pnl, reason, exit_type or "manual", trade_id)
            )

        trade.exit_price = price
        trade.exit_time = now
        trade.status = "closed"
        trade.pnl = pnl
        trade.exit_reason = reason
        trade.exit_type = exit_type or "manual"
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
        # Use clock override if set (backtest uses simulated date)
        if self._clock is not None:
            try:
                today = self._clock().date().isoformat()
            except Exception:
                today = date.today().isoformat()
        else:
            today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_today_pnl(self) -> float:
        # Use simulated date during backtest. Counts P&L from any trade
        # CLOSED on this date — not just entered today (intraday trades
        # entered yesterday don't exist by design, but exit_time is the
        # accurate way to attribute realized P&L to a day).
        if self._clock is not None:
            try:
                today = self._clock().date().isoformat()
            except Exception:
                today = date.today().isoformat()
        else:
            today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT pnl FROM trades WHERE status != 'open' AND exit_time LIKE ?",
                (f"{today}%",)
            ).fetchall()
        return sum((r[0] or 0.0) for r in rows)

    def get_portfolio_summary(self, current_prices: Dict[str, float] = None) -> Dict[str, Any]:
        """Full portfolio snapshot for LLM context."""
        cash = self.get_cash_raw()   # use raw here so we can compute available_cash ourselves
        open_positions = self.get_open_positions()

        holdings_value = 0.0   # long positions: current market value
        short_proceeds = 0.0   # cash credited on open shorts (a liability, not true equity)
        holdings = []
        for pos in open_positions:
            current = current_prices.get(pos.ticker, pos.entry_price) if current_prices else pos.entry_price
            if pos.direction == "short":
                # Unrealized P&L = entry - current (positive if price fell as expected)
                unrealized = (pos.entry_price - current) * pos.quantity
                market_val = current * pos.quantity  # cost to cover right now
                short_proceeds += pos.entry_price * pos.quantity  # proceeds sitting in cash
            else:
                unrealized = (current - pos.entry_price) * pos.quantity
                market_val = current * pos.quantity
                holdings_value += market_val
            holdings.append({
                "trade_id":      pos.id,
                "ticker":        pos.ticker,
                "direction":     pos.direction,
                "qty":           pos.quantity,
                "entry_price":   pos.entry_price,
                "current_price": current,
                "unrealized_pnl": round(unrealized, 2),
                "market_value":  round(market_val, 2),
                "trade_type":    pos.trade_type,
                "held_since":    pos.entry_time,
                "reason":        pos.reason,
                "stop_price":    pos.stop_price,
                "target_price":  pos.target_price,
            })

        # available_cash = raw DB cash minus short sale proceeds (a liability, not equity)
        available_cash = cash - short_proceeds
        # total equity = available cash + long holdings market value
        # (short positions contribute unrealized P&L which is already reflected:
        #  short_proceeds netted out of cash, cover_cost netted out via available_cash calc)
        # Equivalent: cash_raw - cover_cost_at_current + long_holdings
        cover_cost = sum(
            (current_prices.get(pos.ticker, pos.entry_price) if current_prices else pos.entry_price) * pos.quantity
            for pos in open_positions if pos.direction == "short"
        )
        total_value = available_cash + holdings_value - cover_cost + short_proceeds
        # Simplify: total_value = cash(raw) - cover_cost + long_holdings
        total_value = cash - cover_cost + holdings_value
        total_return = total_value - self.starting_capital
        today_pnl = self.get_today_pnl()

        return {
            "cash": round(available_cash, 2),          # cash available for new trades (excl. short proceeds)
            "cash_raw": round(cash, 2),                # raw DB cash (includes short sale proceeds)
            "holdings_value": round(holdings_value, 2),
            "total_value": round(total_value, 2),      # true net equity
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
            pnl=row[10], reason=row[11], exit_reason=row[12],
            # Columns 13-18 added by migration — guard for pre-migration rows
            stop_price=row[13] if len(row) > 13 else None,
            target_price=row[14] if len(row) > 14 else None,
            direction=row[15] if len(row) > 15 else "long",
            conviction=row[16] if len(row) > 16 else None,
            exit_type=row[17] if len(row) > 17 else None,
            llm_model=row[18] if len(row) > 18 else None,
        )
