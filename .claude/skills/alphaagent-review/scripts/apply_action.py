"""
Apply ONE allowlisted change to ONE production AlphaAgent session.

This is the only write path the review loop may use. The allowlist lives here,
in code, so a cleverly worded email cannot widen it — anything not listed below
is rejected before touching disk.

Runs inside the backend container (needs agent.* and dashboard.app):

    ssh ssh-social "docker exec -i alphaagent-backend python3 - '<json>'" \
        < .claude/skills/alphaagent-review/scripts/apply_action.py

    # validate only, change nothing:
    ssh ssh-social "docker exec -i alphaagent-backend python3 - '<json>' --dry-run" < ...

Action JSON (exactly one of):
    {"session": "btc_haiku", "action": "set_universe", "value": ["BTC-USD", "ETH-USD"]}
    {"session": "btc_haiku", "action": "set_universe", "value": []}          # clear → Discovery
    {"session": "btc_haiku", "action": "set_include_news", "value": false}
    {"session": "btc_haiku", "action": "set_extra_system_prompt", "value": "..."}  # "" clears
    {"session": "btc_haiku", "action": "set_interval", "value": 30}
    {"session": "btc_haiku", "action": "set_prompt_version", "value": "v3"}     # "" → registry active
    {"session": "btc_haiku", "action": "stop"}
    {"session": "btc_haiku", "action": "restart"}

Prints one JSON line: {"ok": bool, "applied": ..., "verified": ..., "error": ...}
"""

import json
import os
import signal
import sys
import time

# Only the production fleet the loop reviews. Never user-owned trial sessions.
ALLOWED_SESSIONS = {
    "nse_default", "nse_sonnet", "nse_opus", "vin",
    "btc_haiku", "btc_haiku_strict", "btc_haiku_no_news",
    "btc_haiku_wide", "btc_haiku_long_hold",
}
ALLOWED_ACTIONS = {
    "set_universe", "set_include_news", "set_extra_system_prompt",
    "set_interval", "set_prompt_version", "stop", "restart",
}
CONFIG_ACTIONS = {"set_universe", "set_include_news", "set_extra_system_prompt",
                  "set_interval", "set_prompt_version"}

INTERVAL_RANGE = (5, 240)          # minutes
MAX_PROMPT_CHARS = 2000
MAX_UNIVERSE = 60
STOP_TIMEOUT_S = 60


def _fail(msg):
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


def validate(action: dict, market: str):
    """Return the normalised value, or exit with an error. Pure — no side effects."""
    extra = set(action) - {"session", "action", "value"}
    if extra:
        _fail(f"unexpected keys: {sorted(extra)}")
    kind = action.get("action")
    if kind not in ALLOWED_ACTIONS:
        _fail(f"action '{kind}' is not allowlisted")
    value = action.get("value")

    if kind in ("stop", "restart"):
        if value is not None:
            _fail(f"'{kind}' takes no value")
        return None

    if kind == "set_universe":
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            _fail("universe must be a list of ticker strings")
        tickers = [t.strip().upper() for t in value if t.strip()]
        if len(tickers) > MAX_UNIVERSE:
            _fail(f"universe too large ({len(tickers)} > {MAX_UNIVERSE})")
        # Market isolation: NSE sessions only get NSE tickers, crypto only crypto.
        if market.startswith("nse"):
            bad = [t for t in tickers if not t.endswith(".NS")]
        elif market == "crypto":
            bad = [t for t in tickers if not t.endswith("-USD")]
        else:
            bad = []
        if bad:
            _fail(f"tickers don't belong to market '{market}': {bad}")
        return tickers

    if kind == "set_include_news":
        if not isinstance(value, bool):
            _fail("include_news must be true or false")
        return value

    if kind == "set_extra_system_prompt":
        if not isinstance(value, str):
            _fail("extra_system_prompt must be a string")
        if len(value) > MAX_PROMPT_CHARS:
            _fail(f"prompt override too long ({len(value)} > {MAX_PROMPT_CHARS} chars)")
        return value.strip()

    if kind == "set_prompt_version":
        from agent.prompt_registry import load_registry
        released = set(load_registry()["decision"]["versions"])
        if value != "" and value not in released:
            _fail(f"prompt version must be one of {sorted(released)} or \"\" for the active one")
        return value

    if kind == "set_interval":
        if not isinstance(value, int) or isinstance(value, bool):
            _fail("interval must be an integer number of minutes")
        lo, hi = INTERVAL_RANGE
        if not lo <= value <= hi:
            _fail(f"interval must be between {lo} and {hi} minutes")
        return value


def _stop(session_id):
    from dashboard.app import _is_agent_running
    from agent.session import SESSIONS_DIR

    status = _is_agent_running(session_id)
    if status["running"]:
        pid = status["pid"]
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + STOP_TIMEOUT_S
        while time.time() < deadline and _is_agent_running(session_id)["running"]:
            time.sleep(1)
        if _is_agent_running(session_id)["running"]:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
    # Remove the lock explicitly — a leftover lock makes _auto_restart_agents
    # resurrect the agent within 5 minutes.
    (SESSIONS_DIR / session_id / "agent.lock").unlink(missing_ok=True)
    return not _is_agent_running(session_id)["running"]


def _start(session_id):
    from dashboard.app import _is_agent_running, _start_agent_process, _stamp_started_at

    _start_agent_process(session_id)
    _stamp_started_at(session_id)
    for _ in range(20):            # the child writes its own lock once it boots
        time.sleep(1)
        if _is_agent_running(session_id)["running"]:
            return True
    return False


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    if len(args) != 1:
        _fail("usage: apply_action.py '<action json>' [--dry-run]")
    try:
        action = json.loads(args[0])
    except json.JSONDecodeError as e:
        _fail(f"invalid JSON: {e}")
    if not isinstance(action, dict):
        _fail("action must be a JSON object")

    session_id = action.get("session")
    if session_id not in ALLOWED_SESSIONS:
        _fail(f"session '{session_id}' is not in the production allowlist")

    from agent.session import load_session, save_session
    from dashboard.app import _is_agent_running

    sc = load_session(session_id)
    value = validate(action, sc.market)
    kind = action["action"]
    was_running = _is_agent_running(session_id)["running"]

    if dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "session": session_id,
                          "action": kind, "value": value, "running": was_running}))
        return

    result = {"ok": True, "session": session_id, "action": kind, "value": value}

    if kind in CONFIG_ACTIONS:
        field = {"set_universe": "universe", "set_include_news": "include_news",
                 "set_extra_system_prompt": "extra_system_prompt",
                 "set_interval": "intraday_interval_min",
                 "set_prompt_version": "prompt_version"}[kind]
        before = getattr(sc, field)
        setattr(sc, field, value if value != [] else None)
        save_session(sc)
        after = getattr(load_session(session_id), field)
        result["before"], result["after"] = before, after
        result["verified"] = after == (value if value != [] else None)
        # Running agents read config at startup — restart so the change takes effect.
        if was_running:
            result["restarted"] = _stop(session_id) and _start(session_id)
        result["running"] = _is_agent_running(session_id)["running"]

    elif kind == "stop":
        result["verified"] = _stop(session_id)
        result["running"] = _is_agent_running(session_id)["running"]

    elif kind == "restart":
        result["verified"] = _stop(session_id) and _start(session_id)
        result["running"] = _is_agent_running(session_id)["running"]

    result["ok"] = bool(result.get("verified"))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
