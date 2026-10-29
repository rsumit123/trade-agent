"""Step 3 campaign: Opus proposes setup variants, the backtester scores them (Mac).

    rsync -az ssh-social:/home/rsumit123/alphaagent/data/candles/ data/candles/   # data only, one way
    .venv/bin/python scripts/setup_campaign.py --campaign c1 [--rounds 3] [--per-round 8]
        [--specs my_variants.json]   # skip the API proposer for round 1 and use these specs

Splits (fixed): train < --val-from <= val < --oos-from <= out.
Rounds: evaluate on train+val only (out-of-sample days are not even simulated),
send train/val evidence to the proposer on the VM, validate its specs, repeat.
Final: the top --final-top variants by val net/trade (n >= --min-n in train and
val, positive in both, better than random_entry in both) are run once on the
out-of-sample window. Every variant evaluated goes into
data/bt_results/variants_log.jsonl, and the report states how many were tried.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.setup_bt.data import INDEX_FILES, load  # noqa: E402
from agent.setup_bt.engine import index_context, run_symbol  # noqa: E402
from agent.setup_bt.proposer import evidence  # noqa: E402
from agent.setup_bt.setups import REGISTRY  # noqa: E402
from agent.setup_bt.variants import log_results, parse_specs, tried_count, validate  # noqa: E402
from agent.trade_context import summarise  # noqa: E402

VM = "ssh-social"
VM_DIR = "/home/rsumit123/alphaagent/data/bt_results"

ap = argparse.ArgumentParser()
ap.add_argument("--campaign", required=True)
ap.add_argument("--root", default="data/candles")
ap.add_argument("--interval", default="5minute")
ap.add_argument("--val-from", default="2025-11-01")
ap.add_argument("--oos-from", default="2026-05-01")
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("--per-round", type=int, default=8)
ap.add_argument("--final-top", type=int, default=3)
ap.add_argument("--min-n", type=int, default=30)
ap.add_argument("--specs", help="JSON file of variant specs to use instead of the API proposer in round 1")
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()

val_from, oos_from = date.fromisoformat(a.val_from), date.fromisoformat(a.oos_from)
out_dir = Path("data/bt_results") / a.campaign
out_dir.mkdir(parents=True, exist_ok=True)
ledger = Path("data/bt_results/variants_log.jsonl")
report = []


def say(line=""):
    print(line, flush=True)
    report.append(line)


# Load once. Out-of-sample days are kept aside and only used in the final check.
idx = index_context(load(a.root, a.interval, INDEX_FILES["NIFTY 50"]), load(a.root, a.interval, INDEX_FILES["INDIA VIX"]))
files = sorted(p for p in (Path(a.root) / a.interval).glob("*.csv.gz") if p.name.split(".")[0] not in INDEX_FILES.values())
if a.limit:
    files = files[: a.limit]
data = {}
for p in files:
    df = load(a.root, a.interval, p.name.split(".")[0])
    if df is not None and len(df):
        data[p.name.split(".")[0]] = df
say(f"campaign {a.campaign}: {len(data)} symbols, train < {val_from} <= val < {oos_from} <= out")


def evaluate(setups, oos=False):
    """Simulate on train+val days, or (oos=True) on the out-of-sample window with
    30 trading days of warm-up before it so ATR/prev-close exist from day one."""
    trades = []
    for sym, df in data.items():
        days = df["date"].dt.date
        if oos:
            prior = sorted(set(days[days < oos_from]))
            start = prior[-30] if len(prior) >= 30 else (prior[0] if prior else oos_from)
            part = df[days >= start]
        else:
            part = df[days < oos_from]
        trades += run_symbol(part, sym, setups, idx, oos_from=oos_from, val_from=val_from)
    want = {"out"} if oos else {"train", "val"}
    return [t for t in trades if t["sample"] in want]


def stats(trades, setup_name, split):
    rows = [r for r in summarise([t for t in trades if t["setup"] == setup_name and t["sample"] == split])]
    n = sum(r["n"] for r in rows)
    net = sum(r["net"] for r in rows)
    return {"n": n, "net": round(net, 2), "net_per_trade": round(net / n, 2) if n else None}


all_trades, specs_by_id, history = [], {}, []

# Round 0: the hand-written base setups plus the random control.
t0 = time.time()
base = [REGISTRY[k]() for k in REGISTRY]
tr = evaluate(base)
all_trades += tr
for s in base:
    res = {sp: stats(tr, s.name, sp) for sp in ("train", "val")}
    log_results(ledger, a.campaign, 0, {"id": s.name, "base": s.name, "params": s.params}, res)
    history.append({"id": s.name, "results": res})
    say(f"round 0 {s.name:<28} train {res['train']}  val {res['val']}")
say(f"round 0 took {time.time() - t0:.0f}s")
control = {sp: stats(tr, "random_entry", sp)["net_per_trade"] for sp in ("train", "val")}

for rnd in range(1, a.rounds + 1):
    if rnd == 1 and a.specs:
        specs = parse_specs(Path(a.specs).read_text())
    else:
        inp = {"evidence": evidence(all_trades), "history": history, "n_variants": a.per_round, "round": rnd}
        f = out_dir / f"round_{rnd}_input.json"
        f.write_text(json.dumps(inp))
        subprocess.run(["ssh", VM, f"mkdir -p {VM_DIR}/{a.campaign}"], check=True)
        subprocess.run(["scp", "-q", str(f), f"{VM}:{VM_DIR}/{a.campaign}/"], check=True)
        cmd = (f'docker exec -i -w /app alphaagent-backend python3 - '
               f'/app/data/bt_results/{a.campaign}/{f.name}')
        with open("scripts/setup_propose.py") as script:
            r = subprocess.run(["ssh", VM, cmd], stdin=script, capture_output=True, text=True, timeout=900)
        say(f"round {rnd} proposer: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '(no stderr)'}")
        if r.returncode:
            say(f"proposer failed: {r.stderr[-500:]}")
            break
        specs = json.loads(r.stdout.strip().splitlines()[-1])
    (out_dir / f"round_{rnd}_specs.json").write_text(json.dumps(specs, indent=1))
    setups = []
    for spec in specs[: a.per_round]:
        if spec.get("id") in specs_by_id:
            say(f"  skip duplicate id {spec.get('id')}")
            continue
        s, errs = validate(spec)
        if errs:
            say(f"  rejected {spec.get('id')}: {'; '.join(errs)}")
            continue
        setups.append(s)
        specs_by_id[s.name] = spec
    if not setups:
        say(f"round {rnd}: no valid variants")
        continue
    t0 = time.time()
    tr = evaluate(setups)
    all_trades += tr
    for s in setups:
        res = {sp: stats(tr, s.name, sp) for sp in ("train", "val")}
        log_results(ledger, a.campaign, rnd, specs_by_id[s.name], res)
        history.append({"id": s.name, "spec": {k: v for k, v in specs_by_id[s.name].items() if k != "rationale"},
                        "results": res})
        say(f"round {rnd} {s.name:<28} train {res['train']}  val {res['val']}")
    say(f"round {rnd} took {time.time() - t0:.0f}s")

# Final: pick on train/val only, then touch the out-of-sample window once.
eligible = []
for h in history:
    if h["id"] not in specs_by_id:
        continue
    tr_, va = h["results"]["train"], h["results"]["val"]
    if (tr_["n"] >= a.min_n and va["n"] >= a.min_n and tr_["net_per_trade"] > 0 and va["net_per_trade"] > 0
            and tr_["net_per_trade"] > (control["train"] or 0) and va["net_per_trade"] > (control["val"] or 0)):
        eligible.append(h)
eligible.sort(key=lambda h: -h["results"]["val"]["net_per_trade"])
finalists = eligible[: a.final_top]
n_tried = tried_count(ledger, a.campaign)
say(f"\n{n_tried} variants evaluated in campaign {a.campaign} (incl. round-0 bases). "
    f"{len(eligible)} passed train+val vs control (train {control['train']}, val {control['val']} ₹/trade).")
if finalists:
    setups = [validate(specs_by_id[h["id"]])[0] for h in finalists]
    ctrl = REGISTRY["random_entry"]()
    oos_tr = evaluate(setups + [ctrl], oos=True)
    c_out = stats(oos_tr, "random_entry", "out")
    say(f"OUT-OF-SAMPLE (checked once): random_entry {c_out}")
    for s, h in zip(setups, finalists):
        o = stats(oos_tr, s.name, "out")
        best = max((t["pnl"] for t in oos_tr if t["setup"] == s.name), default=0)
        say(f"  {s.name:<28} out {o}  (train {h['results']['train']['net_per_trade']}, "
            f"val {h['results']['val']['net_per_trade']}; best single trade gross ₹{best:.0f})")
        log_results(ledger, a.campaign, 99, {**specs_by_id[s.name], "final_oos": True}, {"out": o})
else:
    say("No variant beat the control in both train and val; out-of-sample left untouched.")
(out_dir / "summary.txt").write_text("\n".join(report) + "\n")
say(f"report: {out_dir / 'summary.txt'}")
