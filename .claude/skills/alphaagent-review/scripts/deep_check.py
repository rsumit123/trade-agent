"""
Read-only health checks the digest can't see. Run before every review.

The digest reports P&L; it can't tell "the agent chose not to trade" from
"the agent couldn't think". Both failures this checks for were real and went
unnoticed for months while every cycle logged "ok":

  - LLM calls failing (OpenRouter 402 out of credit since April; a delisted
    free model returning 404)
  - learning broken (Claude 5.x thinking blocks crashed every trade reflection)
  - indicators silently missing (30 days of history left EMA50/trend None)

Runs inside the backend container and changes nothing:

    ssh ssh-social "docker exec -i alphaagent-backend python3 -" \
        < .claude/skills/alphaagent-review/scripts/deep_check.py

Prints one JSON object: {"generated_at", "openrouter", "indicators", "sessions": {...},
"problems": [...]}. Every entry in "problems" is something the review must report.
"""

import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/app")

SESSIONS = [
    "nse_default", "nse_sonnet", "vin",
    "btc_haiku", "btc_haiku_strict", "btc_haiku_no_news",
    "btc_haiku_wide", "btc_haiku_long_hold",
]
WINDOW_H = 24
LOG_TAIL_BYTES = 8_000_000
STALE_TRADE_DAYS = 5          # running agent with no trade for this long gets flagged
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[(\w+)\] ([\w.]+): (.*)$")
# Collapse numbers/prices/ids so repeated errors group together.
NOISE = re.compile(r"\d+(\.\d+)?")
# Third-party request logs (news search engines etc.) aren't agent problems.
IGNORE_LOGGERS = {"primp", "httpx", "httpcore", "urllib3", "yfinance", "peewee"}

now = datetime.now(timezone.utc)
since = now - timedelta(hours=WINDOW_H)
problems = []


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - LOG_TAIL_BYTES))
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def check_logs(sid: str, sdir: Path) -> dict:
    levels, errors, http = Counter(), Counter(), Counter()
    cycles = llm_errors = 0
    for raw in _tail(sdir / "agent.log").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        ts = datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if ts < since:
            continue
        level, logger, msg = m[2], m[3], m[4]
        if "Starting decision cycle" in msg:
            cycles += 1
        if "LLM API error" in msg or "Cycle failed — LLM unavailable" in msg:
            llm_errors += 1
        hm = re.search(r'POST https://(openrouter\.ai|api\.anthropic\.com)\S* "HTTP/1\.1 (\d{3})', msg)
        if hm:
            http[f"{hm[1]} {hm[2]}"] += 1
        if level in ("WARNING", "ERROR", "CRITICAL") and logger.split(".")[0] not in IGNORE_LOGGERS:
            levels[level] += 1
            errors[f"[{level}] {logger}: {NOISE.sub('#', msg)[:160]}"] += 1
    out = {"cycles_24h": cycles, "llm_errors_24h": llm_errors, "llm_http": dict(http),
           "warn_err_counts": dict(levels), "top_problems": errors.most_common(6)}
    if cycles and llm_errors >= max(1, cycles // 2):
        problems.append(f"{sid}: {llm_errors}/{cycles} cycles had LLM errors in {WINDOW_H}h")
    bad_http = {k: v for k, v in http.items() if not k.endswith(" 200")}
    if bad_http:
        problems.append(f"{sid}: non-200 LLM responses {bad_http}")
    for msg, n in errors.most_common():
        # News-search misses are routine; errors and failed LLM/learning steps are not.
        if "agent.web_research" in msg:
            continue
        if msg.startswith("[ERROR]") or "failed" in msg.lower():
            problems.append(f"{sid}: {n}× {msg}")
    return out


def check_learning(sid: str, sdir: Path) -> dict:
    text = _tail(sdir / "journal.md")
    entries = re.split(r"\n### ", text)
    recent = [e for e in entries if e[:10] >= since.strftime("%Y-%m-%d")]
    exits = [e for e in recent if "**EXIT**" in e]
    broken = [e for e in exits if "reflection unavailable" in e]
    if broken:
        problems.append(f"{sid}: {len(broken)}/{len(exits)} recent trade reflections failed (learning loop broken)")
    return {"recent_exits": len(exits), "failed_reflections": len(broken)}


def check_trades(sid: str, sdir: Path, running: bool) -> dict:
    try:
        con = sqlite3.connect(f"file:{sdir / 'trades.db'}?mode=ro", uri=True)
        last, total = con.execute("select max(entry_time), count(*) from trades").fetchone()
        con.close()
    except Exception as e:
        return {"error": str(e)}
    days = None
    if last:
        days = (now - datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone(timezone.utc)).days
    if running and (last is None or days >= STALE_TRADE_DAYS):
        problems.append(f"{sid}: running but last trade {'never' if last is None else f'{days} days ago ({last[:10]})'}"
                        " — check whether it can trade at all, not just whether it chose not to")
    return {"total_trades": total, "last_trade": last, "days_since_last_trade": days}


def check_openrouter() -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"configured": False}
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/credits",
                                     headers={"Authorization": f"Bearer {key}"})
        d = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        remaining = round(d["total_credits"] - d["total_usage"], 2)
        if remaining < 2:
            problems.append(f"OpenRouter credit is ${remaining} — any OpenRouter session will get 402s")
        return {"configured": True, "remaining_usd": remaining}
    except Exception as e:
        return {"configured": True, "error": str(e)[:200]}


def check_indicators() -> dict:
    """Fetch one live context per data source; every key indicator must be present."""
    from agent.market_data import MarketData
    from agent.kite_auth import KiteAuth
    from agent.kite_data import KiteMarketData

    keys = ("rsi_14", "ema_20", "ema_50", "ema_trend", "macd_hist", "atr_14", "vol_ratio")
    out = {}
    probes = {
        "yfinance:BTC-USD": lambda: MarketData(["BTC-USD"]).get_stock_context("BTC-USD", include_vwap=False),
        "kite:RELIANCE.NS": lambda: KiteMarketData(["RELIANCE.NS"], None, KiteAuth().get_authenticated_client())
                                   .get_stock_context("RELIANCE.NS"),
    }
    for name, fetch in probes.items():
        try:
            ctx = fetch()
            missing = [k for k in keys if ctx.get(k) is None]
            out[name] = {k: ctx.get(k) for k in keys}
            if missing:
                problems.append(f"indicators missing from {name}: {missing}")
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
            problems.append(f"indicator probe {name} failed: {str(e)[:120]}")
    return out


def main():
    import logging
    logging.disable(logging.CRITICAL)        # keep stdout to the single JSON line
    from agent.session import SESSIONS_DIR
    from dashboard.app import _is_agent_running

    sessions = {}
    for sid in SESSIONS:
        sdir = Path(SESSIONS_DIR) / sid
        running = _is_agent_running(sid)["running"]
        if not running:
            problems.append(f"{sid}: NOT RUNNING")
        sessions[sid] = {
            "running": running,
            **check_logs(sid, sdir),
            **check_learning(sid, sdir),
            **check_trades(sid, sdir, running),
        }
    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "openrouter": check_openrouter(),
        "indicators": check_indicators(),
        "sessions": sessions,
    }
    result["problems"] = problems
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
