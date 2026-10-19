"""Read-only per-cycle check of the live mirror + fleet. Places NO orders.
Run: ssh ssh-social "docker exec -i -w /app alphaagent-backend python3 -" < live_check.py
Prints one JSON line: {"ok": bool, "issues": [...], "info": [...]}."""
import json, logging, os, re, sqlite3, sys
from datetime import datetime, timedelta, timezone
logging.disable(logging.CRITICAL)
sys.path.insert(0, "/app")
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
today = now.strftime("%Y-%m-%d")
since_utc = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
D = "/app/sessions/nse_opus_live"
AGENTS = ["nse_opus", "nse_opus_live", "nse_sonnet", "nse_haiku", "nse_default", "vin"]
BAD = {"stuck", "unknown", "closed_outside_mirror", "closed_by_oco_unconfirmed"}
NOTE = {"missed", "rejected", "blocked", "skipped"}
issues, info = [], []

# 1. agents alive + recent errors
for s in AGENTS:
    try:
        raw = open(f"/app/sessions/{s}/agent.lock").read().strip()
        try:
            pid = json.loads(raw)["pid"]
        except Exception:
            pid = raw
        if not os.path.exists(f"/proc/{pid}"):
            issues.append(f"{s} NOT RUNNING (pid {pid})")
    except FileNotFoundError:
        issues.append(f"{s} has no agent.lock (stopped)")
    try:
        with open(f"/app/sessions/{s}/agent.log", "rb") as f:
            f.seek(max(0, os.path.getsize(f.name) - 2_000_000))
            tail = f.read().decode("utf-8", "replace").splitlines()
        errs = [l for l in tail if l[:19] >= since_utc and ("[ERROR]" in l or "Traceback" in l or "[CRITICAL]" in l)]
        transient = [l for l in errs if "kite_data" in l and re.search(r"timed out|Connection aborted|reset by peer|Max retries", l)]
        errs = [l for l in errs if l not in transient]
        if len(transient) >= 3:
            issues.append(f"{s}: {len(transient)} Kite network errors in 20 min (data feed flaky)")
        elif transient:
            info.append(f"{s}: {len(transient)} transient Kite timeout(s)")
        if errs:
            issues.append(f"{s}: {len(errs)} error lines in 20 min, e.g. {errs[-1][20:220]}")
    except FileNotFoundError:
        pass

# 2. live ledger
legs, alerts = {}, []
for l in open(f"{D}/live_orders.jsonl"):
    r = json.loads(l)
    if r.get("date") != today:
        continue
    if r["event"] == "leg":
        legs[str(r["leg"]["paper_trade_id"])] = r["leg"]
    elif r["event"] in ("alert", "failure") and r.get("ts", "") >= (now - timedelta(minutes=20)).isoformat():
        alerts.append(r)
for a in alerts:
    issues.append(f"ledger {a['event']}: {json.dumps(a)[:300]}")
if os.path.exists(f"{D}/LIVE_KILL"):
    info.append("LIVE_KILL is set (no new real entries)")

# 3. paper open trades must have a leg
con = sqlite3.connect(f"file:{D}/trades.db?mode=ro", uri=True)
cash, cap = con.execute("select cash, starting_capital from account").fetchone()
if cap <= 0:
    issues.append(f"paper account starting_capital={cap} cash={cash}")
paper = con.execute("select id,ticker,direction,quantity,status,pnl,stop_price from trades where entry_time>=?",
                    ((now.replace(hour=0, minute=0) - timedelta(hours=5, minutes=30)).isoformat(),)).fetchall()
for tid, tk, dr, q, st, pnl, stop in paper:
    leg = legs.get(str(tid))
    if not leg:
        issues.append(f"paper #{tid} {tk} {dr} {q} has NO live leg")
        continue
    ls = leg.get("status")
    if ls in BAD:
        issues.append(f"#{tid} {tk} leg status {ls}: {leg.get('note')}")
    elif ls in NOTE:
        info.append(f"#{tid} {tk} leg {ls}: {leg.get('note')}")
    if st == "open" and ls == "open" and stop and leg.get("stop") and abs(float(stop) - float(leg["stop"])) > 0.051 * 2 + float(stop) * 0.0005:
        issues.append(f"#{tid} {tk} paper stop {stop} != leg stop {leg['stop']}")
    if st == "closed" and ls not in ({"closed"} | NOTE) and ls != "exiting":
        issues.append(f"#{tid} {tk} paper closed but leg {ls}")
    if st == "open" and ls == "closed":
        info.append(f"#{tid} {tk}: real closed (exit ₹{leg.get('exit_fill')}, real ₹{leg.get('real_pnl')}) but paper still open — divergence")
    if st == "closed" and ls == "exiting":
        info.append(f"#{tid} {tk} paper closed, real exit in progress")

# 4. Groww: net per symbol vs open legs, stops resting
from agent.groww_broker import GrowwBroker
b = GrowwBroker()
pos = {k: v for k, v in b.positions().items() if v}
orders = (b._call("get_order_list", segment="CASH") or {}).get("order_list", [])
by_id = {o.get("groww_order_id"): o for o in orders}
want = {}
for k, leg in legs.items():
    if k.startswith("smoke") or leg.get("status") not in ("open", "exiting", "entry_working"):
        continue
    sign = 1 if leg["direction"] == "long" else -1
    want[leg["symbol"]] = want.get(leg["symbol"], 0) + sign * int(leg.get("filled_qty") or 0)
    if leg.get("status") == "open":
        o = by_id.get(leg.get("sl_order"))
        if not o:
            issues.append(f"{leg['symbol']} open leg #{k}: NO stop order at Groww")
        elif o.get("order_status") not in ("TRIGGER_PENDING", "OPEN", "ACKED", "NEW", "APPROVED", "MODIFICATION_REQUESTED"):
            issues.append(f"{leg['symbol']} #{k}: stop order is {o.get('order_status')} {o.get('remark') or ''}")
        elif leg.get("stop") and abs(float(o.get("trigger_price") or 0) - float(leg["stop"])) > 0.11:
            issues.append(f"{leg['symbol']} #{k}: Groww trigger {o.get('trigger_price')} != leg stop {leg['stop']}")
for sym in set(want) | set(pos):
    if sym not in want:
        info.append(f"{sym} {pos[sym]:+d} at Groww with no mirror leg (your manual trade?)")
    elif pos.get(sym, 0) != want[sym]:
        issues.append(f"{sym}: Groww net {pos.get(sym, 0):+d} != mirror {want[sym]:+d}")

real = sum(float(l.get("real_pnl") or 0) for l in legs.values() if l.get("status") == "closed")
open_legs = [f"{l['symbol']} {l['direction']} {l.get('filled_qty')}" for k, l in legs.items()
             if not k.startswith("smoke") and l.get("status") in ("open", "exiting")]
info.append(f"open real: {open_legs or 'none'}; realised real net today ₹{real:+.2f} (cap -₹1,500)")
if real <= -1200:
    issues.append(f"realised real loss ₹{real:.0f} is near the ₹1,500 daily cap")
print(json.dumps({"ok": not issues, "at": now.strftime("%H:%M"), "issues": issues, "info": info}))
