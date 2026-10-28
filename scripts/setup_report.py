"""Per-setup results across sessions (read-only).

Run in the container:
    ssh ssh-social "docker exec -i -w /app alphaagent-backend python3 - [--by regime] [--since 2026-10-29] [sessions...]" \
        < scripts/setup_report.py

Groups closed trades by setup x direction (and optionally one entry-context
field: regime, time_bucket, liquidity, sector, ...). Net = P&L minus
estimated intraday charges. Untagged trades predate setup tagging."""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from agent.trade_context import summarise  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("sessions", nargs="*")
ap.add_argument("--by")
ap.add_argument("--since", default="")
a = ap.parse_args(sys.argv[1:])

root = Path("/app/sessions")
names = a.sessions or sorted(p.parent.name for p in root.glob("*/trades.db"))
for name in names:
    db = root / name / "trades.db"
    if not db.exists():
        continue
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        cols = {r[1] for r in c.execute("PRAGMA table_info(trades)")}
        if "setup" not in cols:
            continue
        trades = [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL AND entry_time >= ?", (a.since,))]
    rows = summarise(trades, a.by)
    if not rows:
        continue
    print(f"== {name} ({sum(r['n'] for r in rows)} closed trades)")
    hdr = f"{'setup':<18}{'dir':<6}" + (f"{a.by:<14}" if a.by else "") + \
        f"{'n':>4}{'win%':>7}{'net win%':>9}{'net ₹':>10}{'₹/trade':>9}{'avg win':>9}{'avg loss':>10}  exits"
    print(hdr)
    for r in rows:
        print(f"{r['setup']:<18}{r['direction']:<6}" + (f"{str(r[a.by]):<14}" if a.by else "") +
              f"{r['n']:>4}{r['win_rate']:>7}{r['net_win_rate']:>9}{r['net']:>10}{r['net_per_trade']:>9}"
              f"{str(r['avg_net_win']):>9}{str(r['avg_net_loss']):>10}  {r['exits']}")
    print()
