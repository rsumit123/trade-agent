#!/usr/bin/env python3
"""
End-to-end test of the live mirror against REAL Groww with ONE share of ITC.

Drives the real code path (LivePortfolio → LiveMirror → GrowwBroker) with a
throwaway ledger directory, never the nse_opus_live session:
    [smoke test] → paper BUY 1 → real entry + protective SL → level change
    (SL modify, verified) → paper SELL → real exit → EOD report.

Hard guards: symbol ITC only, quantity 1 (not configurable), market hours
09:20–15:00 IST, and an explicit --yes-real-orders flag. Ctrl-C flattens.

Run inside the backend container (credentials come from the env):
    docker exec -it alphaagent-backend python scripts/live_mirror_e2e.py --yes-real-orders --smoke
"""

import argparse
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.groww_broker import GrowwBroker, GrowwInstruments          # noqa: E402
from agent.live_mirror import LiveMirror, LivePortfolio, round_to_tick  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
ALLOWED_SYMBOLS = {"ITC"}
QTY = 1                                   # hard-coded on purpose


def log(msg):
    print(f"[{datetime.now(IST):%H:%M:%S}] {msg}", flush=True)


def wait(mirror, leg_id, until, timeout_s, label):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        mirror.tick()
        leg = mirror.legs.get(str(leg_id)) or {}
        if leg.get("status") in until:
            return leg
        time.sleep(2)
    leg = mirror.legs.get(str(leg_id)) or {}
    log(f"⚠️  {label}: still {leg.get('status')} after {timeout_s}s")
    return leg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes-real-orders", action="store_true", help="required: this places REAL orders")
    ap.add_argument("--symbol", default="ITC")
    ap.add_argument("--smoke", action="store_true", help="run the daily smoke test first (recommended)")
    ap.add_argument("--skip-smoke", action="store_true", help="treat the smoke test as passed (not recommended)")
    ap.add_argument("--telegram", action="store_true", help="also send alerts to Telegram")
    args = ap.parse_args()

    sym = args.symbol.upper().replace(".NS", "")
    if sym not in ALLOWED_SYMBOLS:
        sys.exit(f"refusing: symbol {sym} not in {sorted(ALLOWED_SYMBOLS)}")
    if not args.yes_real_orders:
        sys.exit("refusing: pass --yes-real-orders (this places REAL orders on Groww)")
    if args.smoke == args.skip_smoke:
        sys.exit("choose exactly one of --smoke / --skip-smoke")
    now = datetime.now(IST)
    if now.weekday() >= 5 or not ("09:20" <= f"{now:%H:%M}" <= "15:00"):
        sys.exit(f"refusing: outside 09:20–15:00 IST on a weekday ({now:%a %H:%M})")
    ticker = f"{sym}.NS"

    workdir = Path(tempfile.mkdtemp(prefix="mirror_e2e_"))
    log(f"ledger dir: {workdir}")

    def alert(text):
        log(f"ALERT {text}")
        if args.telegram:
            from agent.health import send_telegram
            send_telegram(text)

    broker = GrowwBroker()
    portfolio_db = str(workdir / "paper.db")
    reader = None

    def paper_open():
        return {str(p.id) for p in reader.get_open_positions()} if reader else set()

    mirror = LiveMirror(broker, "e2e_mirror", workdir, instruments=GrowwInstruments(), alert=alert,
                        paper_open_ids=paper_open)
    if not mirror.acquire_lock():
        sys.exit("refusing: couldn't take the live lock for the temp dir")
    pf = LivePortfolio(portfolio_db, 100_000, mirror)       # synchronous worker: calls run inline
    reader = pf
    trade = None
    try:
        if args.smoke:
            log("smoke test …")
            r = mirror.smoke_test(ticker)
            log(f"smoke: passed={r['passed']} — {r['reason']}")
            if not r["passed"]:
                sys.exit(1)
        else:
            mirror._smoke = (mirror._today(), True)
            log("smoke test SKIPPED (--skip-smoke)")

        q = broker.quote(sym)
        ltp = q.get("ltp")
        if not ltp:
            sys.exit(f"no LTP for {sym}: {q}")
        tick = mirror._tick_size(ticker, ltp)
        stop = round_to_tick(ltp * 0.98, tick, False)
        target = round_to_tick(ltp * 1.02, tick, True)
        log(f"{sym} LTP ₹{ltp} → paper BUY {QTY}, stop ₹{stop}, target ₹{target}")
        trade = pf.execute_buy(ticker, QTY, ltp, "intraday", reason="e2e", stop_price=stop, target_price=target)
        leg = wait(mirror, trade.id, ("open", "missed", "rejected", "blocked", "skipped"), 45, "entry")
        log(f"entry: {leg.get('status')} fill ₹{leg.get('entry_fill')} SL {leg.get('sl_order')} "
            f"({leg.get('protect_status')}) {leg.get('note') or ''}")
        if leg.get("status") != "open":
            return

        new_stop = round_to_tick(ltp * 0.985, tick, False)
        log(f"level change: stop → ₹{new_stop}")
        pf.update_levels(trade.id, stop_price=new_stop)
        leg = mirror.legs[str(trade.id)]
        if leg.get("sl_order"):
            st = broker.order_status(leg["sl_order"])
            log(f"SL now: {st['status']} trigger ₹{st.get('trigger_price')} limit ₹{st.get('price')}")

        px = broker.quote(sym).get("ltp") or ltp
        log(f"paper SELL at ₹{px}")
        pf.execute_sell(trade.id, px, "e2e exit", exit_type="manual")
        leg = wait(mirror, trade.id, ("closed", "stuck"), 90, "exit")
        log(f"exit: {leg.get('status')} fill ₹{leg.get('exit_fill')} real net ₹{leg.get('real_pnl')} "
            f"{leg.get('note') or ''}")
    except KeyboardInterrupt:
        log("Ctrl-C — flattening")
        mirror.liquidate("e2e interrupted")
        for _ in range(30):
            if not mirror.busy():
                break
            time.sleep(2)
            mirror.tick()
    finally:
        try:
            log(f"Groww net {sym}: {broker.net_position(sym)}")
        except Exception as e:
            log(f"position read failed: {e}")
        log("EOD report:\n" + mirror.eod_report())
        log(f"ledger: {mirror.ledger_path}")
        mirror.release_lock()


if __name__ == "__main__":
    main()
