# Zerodha Kite Connect API — Integration Guide

How AlphaAgent talks to Zerodha's Kite Connect API for live NSE market data
(quotes, OHLC, intraday + historical candles, instrument master). Audience:
other agents/developers who need to consume the same live feed.

---

## Status

Last verified live on **2026-06-24** — full auto-login + `kite.quote(...)` against
RELIANCE/TCS/INFY returned current LTPs. The integration is healthy.

> History: between roughly **2026-05-24 and 2026-06-24** the integration was
> down — Zerodha returned `Invalid api_key` from `/connect/login` (login + 2FA
> succeeded; the key itself was rejected) because the ₹500/month Kite Connect
> subscription had lapsed. Reactivating the app at
> <https://developers.kite.trade/apps/> restored it with no code change. If you
> see the same symptom in the future, check the subscription first — it is the
> overwhelmingly likely cause.

---

## 1. What we use Kite for

| Need | Kite endpoint | Wrapper in this repo |
|---|---|---|
| Real-time quote (LTP, OHLC of the day, depth) | `kite.quote([...])`, `kite.ltp([...])`, `kite.ohlc([...])` | `KiteMarketData.get_current_prices`, `get_watchlist_summary` |
| Historical candles (1m, 5m, 15m, day) | `kite.historical_data(instrument_token, from_dt, to_dt, interval)` | `KiteMarketData.get_stock_context`, `get_intraday_data` |
| Instrument master (every tradable symbol) | `kite.instruments("NSE")` | Cached to `sessions/_nse_instruments.json` by `PreMarketScanner` |
| Margins (for paper-trading sizing) | `kite.margins()` | not used (we paper-trade) |
| Order placement | `kite.place_order(...)` | **not wired** — AlphaAgent is paper-only |

---

## 2. Credentials & environment

All credentials live in the VM's `.env` only (never in code, never in git).
Per `CLAUDE.md`:

| Env var | Purpose |
|---|---|
| `KITE_API_KEY` | App key from <https://developers.kite.trade/apps/>. |
| `KITE_API_SECRET` | App secret (paired with key). |
| `KITE_USER_ID` | Zerodha login user id (e.g. `LV6119`). |
| `KITE_PASSWORD` | Zerodha login password (the human one). |
| `KITE_TOTP_SECRET` | Base32 TOTP secret from the Kite 2FA setup page (the string behind the QR code, **not** the 6-digit code). |

> **Never** commit any of these. The deploy script (`./deploy-prod.sh backend`)
> uses an explicit `--include` rsync and excludes `.env.local` — see
> `CLAUDE.md` for the full rules.
>
> **The actual API key/secret are NOT in this doc by design.** They live in
> the VM's `.env` only. To retrieve them: SSH to the VM (`ssh ssh-social`) and
> `cat /home/rsumit123/alphaagent/.env`, or look them up in the Kite developer
> console at <https://developers.kite.trade/apps/>. Agents running on the VM
> already have them in `os.environ` — just read them from there.

The Python deps you need: `kiteconnect`, `pyotp`, `requests`. Already in our
`requirements.txt`.

---

## 3. Authentication flow (`agent/kite_auth.py`)

### 3.0 First-time setup (one-time, manual)

Before any code runs, the Kite app and the Zerodha account both need to be
prepared. Do this once per Kite Connect app:

1. **Active Zerodha trading account** with the user_id + password you'll script
   against (e.g. `LV6119`). The Kite Connect API also requires an **active
   Kite Connect subscription** (₹500/month, billed monthly from the developer
   console — when it lapses, the symptom is the `Invalid api_key` error
   described in the Status section).

2. **Create a Kite Connect app** at <https://developers.kite.trade/apps/>:
   - Type: **Connect**.
   - **Redirect URL: `http://127.0.0.1`** (or any URL whose host contains
     `127.0.0.1` or `localhost`). This is **load-bearing** — our auto-login
     watches the redirect chain for a Location whose URL contains one of
     those hosts and plucks `request_token` from its query string. If you
     register a different host (e.g. your production domain), `_auto_login`
     will silently fail with `Failed to extract request_token from Kite
     redirect`.
   - **Postback URL**: leave blank.
   - After creating, the app page shows the **API Key** → `KITE_API_KEY` and,
     under "View API secret", the **API Secret** → `KITE_API_SECRET`.

3. **Enable TOTP 2FA on the Zerodha account** (not the developer console):
   - Log into <https://kite.zerodha.com>, open *Account → Settings → Password
     & Security → External 2FA TOTP*.
   - Scan the QR with any TOTP app (Authy, 1Password, Google Authenticator).
   - On the same screen, click **"Can't scan? Use secret key"** (wording
     varies). The string it reveals is the **base32 seed** — that is the
     value that goes into `KITE_TOTP_SECRET`. The 6-digit code your authenticator
     shows is *derived* from this seed; we re-derive it every login via
     `pyotp.TOTP(seed).now()`. Do **not** put the 6-digit code into the env.
   - Once TOTP is enabled, Zerodha will require it for every login — including
     your own. Keep the seed safe.

4. **Populate the VM `.env`** with the five vars from Section 2 and restart
   the container. `KiteAuth().get_authenticated_client()` should now succeed
   on the next call.

Re-do step 2 if you regenerate the API key/secret. Re-do step 3 only if you
reset TOTP on the Zerodha account (rare). Otherwise the setup is permanent
until billing lapses.

### 3.1 Runtime flow

Zerodha's official flow is interactive (browser login → callback URL receives
a `request_token`). We automate it by impersonating the browser:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. POST https://kite.zerodha.com/api/login                               │
│      data: user_id, password           → JSON {data.request_id}          │
│                                                                          │
│ 2. POST https://kite.zerodha.com/api/twofa                               │
│      data: user_id, request_id,        → 2FA authenticated session       │
│            twofa_value=pyotp.TOTP(...).now(), twofa_type='totp'          │
│                                                                          │
│ 3. GET  https://kite.zerodha.com/connect/login?api_key=<KEY>&v=3         │
│      (same session, allow_redirects=False)                               │
│      → 302 chain; pluck `request_token` from any Location whose host is  │
│        our callback (default redirect_uri the app registered with Kite). │
│                                                                          │
│ 4. KiteConnect(api_key=KEY).generate_session(request_token, api_secret)  │
│      → {access_token, ...}                                               │
│                                                                          │
│ 5. KiteConnect(api_key=KEY).set_access_token(access_token)               │
│      ← this is the live, authenticated client.                           │
└──────────────────────────────────────────────────────────────────────────┘
```

`KiteAuth.get_authenticated_client()` runs the whole flow and returns a ready
`KiteConnect` instance.

### Caching (daily rotation)

Zerodha invalidates every access_token at **06:00 IST** every day, regardless
of when it was issued. So we cache the token to `sessions/_kite_token.json`
with the next-rotation timestamp:

```json
{
  "access_token": "<opaque>",
  "expires_at":    "2026-06-25T06:00:00+05:30"
}
```

`KiteAuth._is_expired(token)` uses tz-aware UTC comparison
(`datetime.now(timezone.utc) >= exp`). The earlier naive-vs-aware mismatch
caused a 5h30m drift bug — see commit `b2ef819` if you need history.

### Mid-process re-auth (handling token revocation while running)

A long-lived agent process holds a `KiteConnect` client in memory. When the
daily rotation happens, every subsequent call fails with `"Incorrect api_key
or access_token"` or a Cloudflare `"Just a moment..."` interstitial. We
handle this via `KiteMarketData._call_with_retry` (`agent/kite_data.py`):

```python
def _is_auth_error(self, exc) -> bool:
    msg = str(exc).lower()
    return (
        "incorrect api_key" in msg
        or "access_token" in msg
        or "token is invalid" in msg
        or "just a moment" in msg    # CF challenge page
    )

def _call_with_retry(self, name, fn):
    try:
        return fn()
    except Exception as e:
        if not self._is_auth_error(e) or self._auth is None:
            raise
        logger.warning(f"Kite auth error on {name}; re-authenticating...")
        # Force a fresh login (deletes the cache then re-runs the auto-login)
        self._auth.invalidate_cache()
        new_kite = self._auth.get_authenticated_client()
        self.kite = new_kite
        return fn()  # one retry
```

Every Kite API call from `KiteMarketData` is wrapped in `_call_with_retry`.
The pre-market scanner (`agent/premarket_scanner.py`) wraps its own `kite.ohlc`
calls the same way — see commit `a79cbbf` for why missing this in *one* site
broke a session silently for two weeks.

**If you write new Kite-using code, wrap every call.**

### 3.4 End-to-end example for an agent

A complete, runnable script. Read env vars → authenticate → fetch live data
for three stocks → print structured output. Copy-paste into the container
and run as-is (`docker exec alphaagent-backend python3 /tmp/snippet.py`):

```python
#!/usr/bin/env python3
"""Bare-minimum Kite live-data agent. No wrappers, no caching, no retry —
just the raw auth flow + a few data calls so you can see every step."""

import os, datetime as dt
import pyotp, requests
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect

# ── 1. Read credentials from env (never hardcode) ────────────────────────
API_KEY      = os.environ["KITE_API_KEY"]
API_SECRET   = os.environ["KITE_API_SECRET"]
USER_ID      = os.environ["KITE_USER_ID"]
PASSWORD     = os.environ["KITE_PASSWORD"]
TOTP_SECRET  = os.environ["KITE_TOTP_SECRET"]   # base32 seed, not the 6-digit code

# ── 2. Auto-login: password → TOTP → request_token → access_token ────────
s = requests.Session()

# Step A: password login
r = s.post("https://kite.zerodha.com/api/login",
           data={"user_id": USER_ID, "password": PASSWORD})
request_id = r.json()["data"]["request_id"]

# Step B: TOTP 2FA (re-derive 6-digit code from base32 seed)
s.post("https://kite.zerodha.com/api/twofa", data={
    "user_id":    USER_ID,
    "request_id": request_id,
    "twofa_value": pyotp.TOTP(TOTP_SECRET).now(),
    "twofa_type": "totp",
})

# Step C: hit /connect/login on the authenticated session and follow the
# redirect chain. The Location header of the *last* redirect contains
# ?request_token=... — that's what we exchange for an access_token.
# (The redirect URI registered on the Kite app must contain 127.0.0.1
# or localhost — see Section 3.0.)
resp = s.get(f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3",
             allow_redirects=False)
request_token = None
for _ in range(10):
    if resp.status_code not in (301, 302): break
    loc = resp.headers.get("Location", "")
    if "127.0.0.1" in loc or "localhost" in loc:
        request_token = parse_qs(urlparse(loc).query).get("request_token", [None])[0]
        break
    resp = s.get(loc, allow_redirects=False)
assert request_token, "Could not extract request_token — check redirect URI on the Kite app"

# Step D: exchange request_token for access_token
kite = KiteConnect(api_key=API_KEY)
access_token = kite.generate_session(request_token, api_secret=API_SECRET)["access_token"]
kite.set_access_token(access_token)

# (In production: cache `access_token` to a file with an expires_at of
# tomorrow 06:00 IST and reuse it until then — see agent/kite_auth.py.
# Tokens are invalidated daily by Zerodha regardless of issue time.)

# ── 3. Fetch live data ───────────────────────────────────────────────────
print("PROFILE:", kite.profile()["user_id"], kite.profile()["broker"])

# Live quotes (LTP + OHLC + market depth) for a batch of symbols.
# Symbol format for Kite: 'EXCHANGE:TRADINGSYMBOL' — NOT yfinance-style '.NS'.
symbols = ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"]
quote = kite.quote(symbols)
for sym in symbols:
    q = quote[sym]
    print(f"{sym}: last={q['last_price']}  "
          f"ohlc={q['ohlc']}  vol={q['volume_traded']}")

# Lightweight LTP-only (cheaper if you only need the last price)
print("LTP:", kite.ltp(symbols))

# Today's OHLC (for the pre-market scanner pattern)
print("OHLC:", kite.ohlc(symbols))

# Historical candles. instrument_token comes from kite.quote()/kite.instruments();
# interval is one of: minute, 3minute, 5minute, 15minute, 30minute, 60minute, day.
inst_token = quote["NSE:RELIANCE"]["instrument_token"]
candles = kite.historical_data(
    inst_token,
    from_date = dt.datetime.now() - dt.timedelta(days=5),
    to_date   = dt.datetime.now(),
    interval  = "day",
)
print(f"RELIANCE daily candles (last 3 of {len(candles)}):")
for c in candles[-3:]:
    print("  ", c)
```

Expected output (during market hours; values change):

```
PROFILE: LV6119 ZERODHA
NSE:RELIANCE: last=1300.5  ohlc={'open': 1295.0, 'high': 1308.4, 'low': 1290.1, 'close': 1298.7}  vol=4_523_119
NSE:TCS:      last=2080    ohlc={'open': 2075.6, 'high': 2092.3, 'low': 2070.4, 'close': 2079.2}  vol=987_410
NSE:INFY:     last=1047.5  ohlc={...}  vol=...
LTP: {'NSE:RELIANCE': {'instrument_token': 738561, 'last_price': 1300.5}, ...}
OHLC: {...}
RELIANCE daily candles (last 3 of 4):
   {'date': datetime.datetime(2026, 6, 20, 0, 0, tzinfo=<...IST>), 'open': 1291.0, 'high': 1303.5, 'low': 1288.0, 'close': 1298.7, 'volume': 4_120_445}
   ...
```

**This script intentionally skips production niceties** (token caching, mid-
process re-auth, batch sleeps). For real agent code, use the wrappers:

```python
from agent.kite_auth import KiteAuth
from agent.kite_data import KiteMarketData

auth = KiteAuth()                                 # handles cache + tz-aware expiry
kite = auth.get_authenticated_client()            # auto-logs in if cache stale
md   = KiteMarketData(kite, auth=auth, watchlist=["RELIANCE.NS", "TCS.NS"])

prices = md.get_current_prices(["RELIANCE.NS", "TCS.NS"])
ctx    = md.get_stock_context("RELIANCE.NS", include_vwap=True)
```

`KiteMarketData` wraps every call in `_call_with_retry` (mid-process re-auth
on token rotation) and translates ticker formats — see Section 5.

---

## 4. Quick-start

### Get an authenticated client

```python
from agent.kite_auth import KiteAuth

auth = KiteAuth()                          # reads env + cache
kite = auth.get_authenticated_client()     # returns kiteconnect.KiteConnect

# Sanity check
print(kite.profile())   # → {'user_id': 'LV6119', 'email': ..., 'broker': 'ZERODHA'}
```

### Live quotes (LTP + OHLC + depth)

```python
data = kite.quote(["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"])
# Each value contains: instrument_token, last_price, last_quantity,
# average_price, volume, buy_quantity, sell_quantity, ohlc, depth, ...
print(data["NSE:RELIANCE"]["last_price"])
```

Batch up to ~500 symbols per call; use multiple calls for more.

### Lightweight LTP only

```python
ltp = kite.ltp(["NSE:RELIANCE"])      # cheaper than .quote()
print(ltp["NSE:RELIANCE"]["last_price"])
```

### Today's OHLC

```python
ohlc = kite.ohlc(["NSE:RELIANCE"])
# {'NSE:RELIANCE': {'instrument_token': 738561,
#                   'last_price': ..., 'ohlc': {'open':..,'high':..,'low':..,'close':..}}}
```

### Historical candles

```python
import datetime as dt
instrument_token = data["NSE:RELIANCE"]["instrument_token"]
candles = kite.historical_data(
    instrument_token,
    from_date = dt.datetime.now() - dt.timedelta(days=30),
    to_date   = dt.datetime.now(),
    interval  = "day",        # 'minute','3minute','5minute','15minute','30minute','60minute','day'
)
# Returns: [{'date': datetime(..., tzinfo=+05:30), 'open':.., 'high':.., 'low':.., 'close':.., 'volume':..}, ...]
```

> Intraday intervals (`minute`/`5minute`/etc.) only carry back ~60 days.
> `day` candles carry back ~years. Returned timestamps are **tz-aware IST
> (+05:30)**; strip the tz only if you have a deliberate reason (the backtest
> engine does this — see `agent/backtest_data.py`).

### Instrument master (every tradable NSE symbol)

```python
instruments = kite.instruments("NSE")        # one big list, ~10k rows
# Each row: instrument_token, exchange_token, tradingsymbol, name, last_price,
#           expiry, strike, tick_size, lot_size, instrument_type, segment, exchange
```

Heavy call. Cache it. Our scanner stores a filtered subset to
`sessions/_nse_instruments.json` and refreshes weekly — see
`PreMarketScanner._ensure_instrument_cache` / `_refresh_instrument_cache`
(`agent/premarket_scanner.py:73`) for the pattern, including which junk
symbols to drop (ETFs, indices, SME suffixes, etc.).

---

## 5. Symbol conventions

| Context | Format | Example |
|---|---|---|
| Internal watchlist | `<SYMBOL>.NS` (yfinance-style suffix) | `RELIANCE.NS` |
| Kite quote/LTP/OHLC keys | `<EXCHANGE>:<TRADINGSYMBOL>` | `NSE:RELIANCE` |
| Kite historical_data | `instrument_token` (int from instrument master) | `738561` |

`KiteMarketData` converts back and forth — never sprinkle hardcoded prefixes
through new code; route through the wrapper.

---

## 6. Code locations in this repo

| File | What it owns |
|---|---|
| `agent/kite_auth.py` | `KiteAuth` — auto-login, TOTP, token cache, expiry check. |
| `agent/kite_data.py` | `KiteMarketData` — drop-in replacement for the yfinance `MarketData`. All quote/historical access goes through here. Wraps every Kite call in `_call_with_retry` for mid-process re-auth. |
| `agent/market_data.py` | `create_market_data(session, preset)` — factory that returns `KiteMarketData` when `session.data_source == "kite"`, else yfinance. |
| `agent/premarket_scanner.py` | `PreMarketScanner` — uses Kite for the 2-phase scan; also owns the instrument cache. |
| `agent/backtest_data.py` | `BacktestMarketData` — pre-fetches candle data via Kite, then replays it from memory. |
| `agent/runner.py` | Calls all of the above; constructs the auth handle and passes it into the scanner so re-auth works inside both. |

---

## 7. Rate limits + behaviour

Zerodha's published limits (per second, per app):

| Endpoint | Limit |
|---|---|
| `quote`, `ohlc`, `ltp` | 1 req/s (each up to ~500 symbols) |
| `historical_data` | 3 req/s |
| `orders` (we don't use) | 10 req/s |
| Overall | 10 req/s |

The scanner batches OHLC in groups of 500 with 1s sleep between batches —
see `PreMarketScanner.scan_phase1`. Mirror that pattern for any bulk fetch.

**Cloudflare challenges.** When Zerodha is under load, individual responses
can come back as an HTML `"Just a moment..."` page instead of JSON. The
retry helper treats this as an auth-style error and re-auths once; if it
keeps happening, back off and retry later. Don't loop hard against it.

---

## 8. Common gotchas (incidents we've actually hit)

1. **Forgetting to wrap a new Kite call.** Anything not going through
   `_call_with_retry` will die silently when the daily token rotates. The
   pre-market scanner missed this for `kite.ohlc` and one session sat in cash
   for two weeks (commit `a79cbbf`).
2. **tz-naive expiry comparison.** `KiteAuth._is_expired` must compare
   tz-aware datetimes. A naive `datetime.now()` inside the UTC container
   reads ~5h30m behind IST and lets revoked tokens look valid (commit
   `b2ef819`).
3. **TOTP secret vs TOTP code.** `KITE_TOTP_SECRET` is the **base32 seed**
   (long string from the QR/setup screen), not a 6-digit OTP.
4. **Subscription billing.** Kite Connect is ₹500/month and Zerodha disables
   the API key the moment billing fails. Symptom is exactly what we're
   seeing now: login + 2FA succeed, `/connect/login` returns `Invalid
   api_key`. The fix is on Zerodha's side, not in code.
5. **Markets-closed weekends / holidays.** Quotes still work (return the
   last traded values), but `historical_data` for `minute`/`5minute` will
   return empty arrays for non-trading days. Backtests should skip weekends
   explicitly.
6. **Don't blast `instruments("NSE")`.** It downloads ~10k rows. Cache it
   (`sessions/_nse_instruments.json`, refreshed weekly).
7. **Symbol format.** Kite uses `NSE:<TRADINGSYMBOL>` (e.g. `NSE:M&M`,
   `NSE:BAJAJ-AUTO`) — the suffix `.NS` is yfinance's convention and Kite
   will reject it. Route through `KiteMarketData` and you won't have to
   think about this.

---

## 9. Failure / debug runbook

| Symptom | Likely cause | Check |
|---|---|---|
| `Invalid api_key` from `/connect/login` (login + 2FA succeeded) | Kite Connect subscription lapsed or app disabled | <https://developers.kite.trade/apps/> |
| `Failed to extract request_token from Kite redirect` | The redirect URI configured on the app doesn't match what `_auto_login` watches for, or `/connect/login` returned 400 (see above). Trace by re-running step 3 with `allow_redirects=False` and printing each `Location`. | `agent/kite_auth.py:110-127` |
| `Incorrect api_key or access_token` mid-run | Daily 06:00 IST token rotation; in-memory client is stale | Should auto-recover via `_call_with_retry`. If not, look for a Kite call that bypasses the wrapper. |
| `Just a moment...` HTML where JSON expected | Cloudflare challenge under load | Treated as auth error — one re-auth retry, then back off. |
| `pyotp.TOTP(...).now()` keeps failing TOTP step | Wrong secret (using 6-digit code instead of base32 seed) or server clock skew | Verify env var holds the long base32 string; check VM clock against IST. |
| Empty `historical_data` for an intraday interval | Outside the ~60-day window for intraday, weekend/holiday, or symbol not tradeable | Try `day` interval; check `kite.instruments("NSE")` for the symbol. |

For an end-to-end smoke test on the VM:

```bash
ssh ssh-social
docker exec alphaagent-backend python3 -c "
from agent.kite_auth import KiteAuth
kite = KiteAuth().get_authenticated_client()
print(kite.profile())
print(kite.ltp(['NSE:RELIANCE']))
"
```

If that prints the profile + an LTP, the integration is healthy. If it
raises `Invalid api_key`, you're on the subscription path.

---

## 10. References

- KiteConnect Python: <https://github.com/zerodhatech/pykiteconnect>
- HTTP API reference: <https://kite.trade/docs/connect/v3/>
- Developer console (renew / regenerate keys / change redirect URL):
  <https://developers.kite.trade/apps/>
- Status page: <https://status.zerodha.com/>
