"""
Telegram alerts for every trade action a session takes: opens, closes,
stop/target moves, and automatic exits (stop hit, target hit, forced close).

Opt-in per session with a marker file, so it can be switched on or off on
the VM without a restart or deploy:

    touch sessions/<id>/telegram_trade_alerts     # on
    rm    sessions/<id>/telegram_trade_alerts     # off

Sending happens on a daemon thread and never raises into the trading loop.
"""

import logging
import threading
from pathlib import Path
from typing import Optional

from .health import send_telegram

logger = logging.getLogger(__name__)

MARKER_FILE = "telegram_trade_alerts"
MAX_REASON_CHARS = 1500   # Telegram caps a message at 4096 chars


def enabled(session_dir: Optional[Path]) -> bool:
    return session_dir is not None and (Path(session_dir) / MARKER_FILE).exists()


def _money(sym: str, v) -> str:
    return "—" if v is None else f"{sym}{v:,.2f}"


def _reason(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_REASON_CHARS:
        text = text[:MAX_REASON_CHARS] + "…"
    return f"\n\n{text}" if text else ""


def format_trade(session_id: str, sym: str, tool_input: dict, result: dict) -> Optional[str]:
    """Message for a successful place_trade call, or None."""
    if not result.get("success"):
        return None
    action = result.get("action", "")
    reason = _reason(tool_input.get("reason", ""))
    tid = result.get("trade_id")
    if action in ("BUY", "SHORT"):
        icon, side = ("🟢", "LONG") if action == "BUY" else ("🔻", "SHORT")
        qty, price = result.get("quantity"), result.get("price")
        conv = tool_input.get("conviction")
        value = qty * price if qty and price else None
        return (
            f"{icon} {session_id}: OPEN {side} #{tid} {result.get('ticker')}\n"
            f"{qty:g} @ {_money(sym, price)} ({_money(sym, value)})"
            f"{f' · conviction {conv}' if conv else ''}\n"
            f"stop {_money(sym, result.get('stop_price'))} · "
            f"target {_money(sym, result.get('target_price'))}"
            f"{reason}"
        )
    if action in ("SELL", "COVER"):
        pnl = result.get("pnl")
        icon = "✅" if (pnl or 0) > 0 else "❌"
        return (
            f"{icon} {session_id}: CLOSE ({action}) #{tid} {result.get('ticker')}\n"
            f"exit {_money(sym, result.get('exit_price'))} · P&L {_money(sym, pnl)}"
            f"{reason}"
        )
    return None


def format_levels(session_id: str, sym: str, tool_input: dict, result: dict,
                  old_stop=None, old_target=None) -> Optional[str]:
    """Message for a successful update_levels call, or None."""
    if not result.get("success"):
        return None
    parts = []
    if tool_input.get("stop_price") is not None:
        parts.append(f"stop {_money(sym, old_stop)} → {_money(sym, result.get('stop_price'))}")
    if tool_input.get("target_price") is not None:
        parts.append(f"target {_money(sym, old_target)} → {_money(sym, result.get('target_price'))}")
    return (
        f"🔧 {session_id}: LEVELS #{result.get('trade_id')} {result.get('ticker')}\n"
        f"{' · '.join(parts)}"
        f"{_reason(tool_input.get('reason', ''))}"
    )


def format_auto_exit(session_id: str, sym: str, trade) -> Optional[str]:
    """Message for an exit the agent didn't make (stop/target hit, forced close)."""
    if trade is None or trade.exit_price is None:
        return None
    label = {
        "stop_hit": "🛑 STOP HIT",
        "target_hit": "🎯 TARGET HIT",
        "forced_close": "⏰ FORCED CLOSE",
    }.get(trade.exit_type, "🔚 CLOSED")
    return (
        f"{label} {session_id}: #{trade.id} {trade.ticker} {trade.direction or 'long'}\n"
        f"entry {_money(sym, trade.entry_price)} → exit {_money(sym, trade.exit_price)} · "
        f"P&L {_money(sym, trade.pnl)}"
    )


def send(session_dir: Optional[Path], text: Optional[str]) -> None:
    """Send text on a background thread if alerts are on for this session."""
    if not text:
        return
    try:
        if not enabled(session_dir):
            return
        threading.Thread(target=send_telegram, args=(text,), daemon=True).start()
    except Exception as e:
        logger.warning(f"Trade alert failed: {e}")
