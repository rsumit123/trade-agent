"""Run the setup backtester on cached candles (on the Mac, any time).

    # one-way copy of the candle cache from the VM (data only, never .env/tokens)
    rsync -az ssh-social:/home/rsumit123/alphaagent/data/candles/ data/candles/
    .venv/bin/python scripts/setup_bt_run.py [--setups gap_fade,orb_breakout,vwap_cross]
        [--oos-from 2026-05-01] [--notional 10000] [--by regime] [--limit N]

Prints in-sample and out-of-sample per-setup results (net of slippage and
estimated charges) and writes every trade to data/bt_results/<timestamp>.csv.
"""
import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd  # noqa: E402

from agent.setup_bt.data import INDEX_FILES, load  # noqa: E402
from agent.setup_bt.engine import index_context, run_symbol  # noqa: E402
from agent.setup_bt.setups import REGISTRY  # noqa: E402
from agent.trade_context import summarise  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--root", default="data/candles")
ap.add_argument("--interval", default="5minute")
ap.add_argument("--setups", default=",".join(REGISTRY))
ap.add_argument("--oos-from", default="2026-05-01")
ap.add_argument("--notional", type=float, default=10_000)
ap.add_argument("--slippage", type=float, default=0.0005)
ap.add_argument("--by")
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()

oos = date.fromisoformat(a.oos_from)
setups = [REGISTRY[s]() for s in a.setups.split(",")]
idx = index_context(load(a.root, a.interval, INDEX_FILES["NIFTY 50"]), load(a.root, a.interval, INDEX_FILES["INDIA VIX"]))
files = sorted(p for p in (Path(a.root) / a.interval).glob("*.csv.gz")
               if p.name.split(".")[0] not in INDEX_FILES.values())
if a.limit:
    files = files[: a.limit]
t0, trades = time.time(), []
for k, p in enumerate(files, 1):
    sym = p.name.split(".")[0]
    df = load(a.root, a.interval, sym)
    if df is not None and len(df):
        trades += run_symbol(df, sym, setups, idx, a.notional, a.slippage, oos)
print(f"{len(files)} symbols, {len(trades)} trades, {time.time() - t0:.0f}s "
      f"(notional ₹{a.notional:,.0f}, slippage {a.slippage:.2%}/side, out-of-sample from {oos})\n")

lines = [f"{len(files)} symbols, {len(trades)} trades, notional ₹{a.notional:,.0f}, "
         f"slippage {a.slippage:.2%}/side, out-of-sample from {oos}, setups {a.setups}, by {a.by}"]
for sample in ("in", "out"):
    rows = summarise([t for t in trades if t["sample"] == sample], a.by)
    lines.append(f"== {sample.upper()}-OF-SAMPLE")
    lines.append(f"{'setup':<14}{'dir':<6}" + (f"{a.by:<12}" if a.by else "") +
                 f"{'n':>6}{'win%':>7}{'net win%':>9}{'net ₹':>11}{'₹/trade':>9}{'avg win':>9}{'avg loss':>10}")
    for r in rows:
        lines.append(f"{r['setup']:<14}{r['direction']:<6}" + (f"{str(r[a.by]):<12}" if a.by else "") +
                     f"{r['n']:>6}{r['win_rate']:>7}{r['net_win_rate']:>9}{r['net']:>11}{r['net_per_trade']:>9}"
                     f"{str(r['avg_net_win']):>9}{str(r['avg_net_loss']):>10}")
    lines.append("")
print("\n".join(lines))

stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
out = Path("data/bt_results") / f"{stamp}.csv"
out.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(trades).to_csv(out, index=False)
(out.parent / f"{stamp}-summary.txt").write_text("\n".join(lines) + "\n")
print(f"Trades written to {out} (summary: {stamp}-summary.txt)")
