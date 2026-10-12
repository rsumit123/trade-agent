"""
Per-session LLM health: detect agents that are "running" but can't reach their model.

A cycle where every LLM call failed (model delisted, credits exhausted, auth
revoked, provider outage) used to be logged as "ok", so a dead agent looked
healthy for months. Now each cycle is recorded in sessions/<id>/health.json:

    {"consecutive_llm_failures": 3, "last_error": "...", "last_error_at": "...",
     "last_success_at": "...", "alerted": true}

After ALERT_AFTER consecutive failed cycles a Telegram alert is sent (once),
and a recovery message is sent when the next cycle succeeds. The digest API
surfaces the same file so the daily review flags it too.

Telegram is configured via TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID; without
them alerts are skipped. Nothing here may raise into the trading loop.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ALERT_AFTER = 3          # consecutive failed cycles before alerting
HEALTH_FILE = "health.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_health(session_dir: Path) -> dict:
    try:
        return json.loads((Path(session_dir) / HEALTH_FILE).read_text())
    except Exception:
        return {}


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
        return False


def record_cycle(session_dir: Path, session_id: str, model: str,
                 llm_failed: bool, error: Optional[str] = None) -> dict:
    """Update health.json after a decision cycle; alert on threshold / recovery."""
    path = Path(session_dir) / HEALTH_FILE
    h = read_health(session_dir)
    h.setdefault("consecutive_llm_failures", 0)
    h.setdefault("alerted", False)

    try:
        if llm_failed:
            h["consecutive_llm_failures"] += 1
            h["last_error"] = (error or "unknown error")[:500]
            h["last_error_at"] = _now()
            if h["consecutive_llm_failures"] >= ALERT_AFTER and not h["alerted"]:
                h["alerted"] = send_telegram(
                    f"🚨 AlphaAgent '{session_id}' can't reach its LLM "
                    f"({h['consecutive_llm_failures']} failed cycles in a row).\n"
                    f"Model: {model}\nError: {h['last_error'][:300]}"
                )
        else:
            if h["alerted"]:
                send_telegram(f"✅ AlphaAgent '{session_id}' recovered — LLM calls succeeding again ({model}).")
            h["consecutive_llm_failures"] = 0
            h["alerted"] = False
            h["last_success_at"] = _now()

        path.write_text(json.dumps(h, indent=2))
    except Exception as e:
        logger.warning(f"Could not update health file: {e}")
    return h
