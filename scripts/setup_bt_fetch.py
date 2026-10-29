"""Fetch 5-minute candles for the setup backtester (VM, inside the container, AFTER HOURS).

    ssh ssh-social "docker exec -i -w /app alphaagent-backend nice -n 19 python3 - [--years 2] [--universe nifty200] [--limit N] [--force]" \
        < scripts/setup_bt_fetch.py

Resumable: only ranges missing from /app/data/candles/5minute/<SYMBOL>.csv.gz are fetched.
Refuses to run 09:00-15:40 IST on weekdays (Kite's historical rate limit is shared
with the live agents) unless --force. Read-only towards trading: places nothing.
"""
import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
for n in ("urllib3", "kiteconnect"):
    logging.getLogger(n).setLevel(logging.WARNING)

from agent.kite_auth import KiteAuth  # noqa: E402
from agent.sector_data import _fetch_index_csv, parse_constituents_csv  # noqa: E402
from agent.setup_bt.data import INDEX_FILES, fetch_symbol, in_market_hours  # noqa: E402

UNIVERSES = {"nifty200": "ind_nifty200list", "nifty500": "ind_nifty500list"}

ap = argparse.ArgumentParser()
ap.add_argument("--years", type=float, default=2.0)
ap.add_argument("--universe", default="nifty200", choices=list(UNIVERSES))
ap.add_argument("--interval", default="5minute")
ap.add_argument("--limit", type=int, default=0, help="only the first N symbols (smoke test)")
ap.add_argument("--root", default="/app/data/candles")
ap.add_argument("--force", action="store_true")
a = ap.parse_args(sys.argv[1:])

now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
if in_market_hours(now) and not a.force:
    sys.exit(f"Refusing to fetch during market hours ({now:%H:%M} IST): Kite's historical limit is shared "
             "with the live agents. Run after 15:40 or pass --force.")

kite = KiteAuth().get_authenticated_client()
tokens = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}
text = _fetch_index_csv(UNIVERSES[a.universe])
symbols = [t.replace(".NS", "") for t in parse_constituents_csv(text)] if text else []
if not symbols:
    sys.exit("Could not load the universe list")
if a.limit:
    symbols = symbols[: a.limit]

end = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
start = end - timedelta(days=int(a.years * 365))


def hist(token, f, t, iv):
    for attempt in range(4):
        try:
            return kite.historical_data(token, f, t, iv)
        except Exception as e:
            if attempt == 3:
                raise
            logging.warning(f"historical {token} retry {attempt + 1}: {e}")
            time.sleep(2 * (attempt + 1))


jobs = [(name, fname) for name, fname in INDEX_FILES.items()] + [(s, s) for s in symbols]
t0, total, missing = time.time(), 0, []
for k, (name, fname) in enumerate(jobs, 1):
    tok = tokens.get(name)
    if not tok:
        missing.append(name)
        continue
    try:
        n = fetch_symbol(hist, tok, fname, start, end, a.root, a.interval)
        total += n
        if k % 20 == 0 or k == len(jobs):
            logging.info(f"{k}/{len(jobs)} done, {total} new rows, {time.time() - t0:.0f}s")
    except Exception as e:
        logging.error(f"{name}: {e}")
        missing.append(name)
print(f"Done: {len(jobs) - len(missing)}/{len(jobs)} instruments, {total} new rows, "
      f"{start}..{end}, {time.time() - t0:.0f}s. Missing: {missing[:20]}")
