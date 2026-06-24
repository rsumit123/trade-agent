# Zerodha Kite Connect API — Developer Guide

A self-contained guide for calling Zerodha's Kite Connect REST API to fetch
live NSE / BSE market data (quotes, OHLC, intraday + historical candles,
instrument master). Aimed at any developer or autonomous agent that has a
Kite Connect app and a Zerodha trading account — no other infrastructure
assumed.

What you need before reading further:

- A Zerodha trading account (active, with a `user_id` like `XX1234`).
- An active **Kite Connect subscription** (₹2000 one-time + ₹500/month).
  Without it, Zerodha returns `Invalid api_key` from `/connect/login` even
  though every other step works.
- Python 3.9+ and `pip install kiteconnect pyotp requests`.

---

## 1. What the API provides

| Need | Endpoint (Python SDK call) |
|---|---|
| Real-time last price for a list of symbols | `kite.ltp([...])` |
| Today's OHLC + LTP | `kite.ohlc([...])` |
| Full quote (LTP + OHLC + market depth + volumes) | `kite.quote([...])` |
| Historical candles (1m / 5m / 15m / 30m / 60m / day) | `kite.historical_data(token, from_dt, to_dt, interval)` |
| Master list of every tradable symbol (token, name, lot size, expiry, …) | `kite.instruments("NSE")` |
| Account profile | `kite.profile()` |
| Margins / funds | `kite.margins()` |
| Place / modify / cancel orders | `kite.place_order(...)`, etc. |

This guide focuses on **data** (quotes + historical). Order placement uses
the same authenticated client.

---

## 2. Credentials

You will be issued five values out-of-band (set as environment variables in
whatever environment you run in). The script reads them via `os.environ`:

| Variable | What it is |
|---|---|
| `KITE_API_KEY` | Key for the pre-configured Kite Connect app. |
| `KITE_API_SECRET` | Secret paired with that key. |
| `KITE_USER_ID` | Zerodha login user id (e.g. `XX1234`). |
| `KITE_PASSWORD` | Zerodha login password (the human one). |
| `KITE_TOTP_SECRET` | Base32 **seed** of the account's TOTP 2FA — a long string. **Not** the rolling 6-digit code. |

**Storage policy.** All five are secrets, same class as a database password.
Read them from environment variables (or a secret manager); never hardcode
in source, never commit to git. **This guide does not, and will not, contain
any actual key values** — they are supplied to the agent out-of-band.

---

## 3. About the existing Kite app

You will be given credentials for an **existing, pre-configured Kite Connect
app** — do not create a new one. The app and Zerodha account are already
set up; the agent's job is just to use them. This section describes how
the existing app is configured so you understand why the auth flow in
Section 4 looks the way it does.

### 3.1 App configuration (already done — for context only)

- **Type:** `Connect`.
- **Redirect URL:** `http://127.0.0.1` (host is `127.0.0.1`).
  *Why this matters:* the scripted login in Section 4 watches the OAuth
  302 chain for a `Location` header whose host contains `127.0.0.1` or
  `localhost`, and plucks `request_token` from its query string. The
  existing app is registered with `127.0.0.1` so the flow works headlessly,
  with no callback server actually needed.
- **Postback URL:** none.
- **Subscription:** active (₹500/month). If it ever lapses, the symptom is
  `Invalid api_key` from `/connect/login` — see the debug runbook.

You will not need to touch the developer console. If the API key/secret
ever rotate, you will be re-issued new env values out-of-band.

### 3.2 Account TOTP (already done — for context only)

The Zerodha account already has TOTP 2FA enabled. The `KITE_TOTP_SECRET`
you receive is the **base32 seed** behind that TOTP — a long string, not
the 6-digit code. The auth code re-derives the 6-digit value on every
login via `pyotp.TOTP(seed).now()`. **Never** put a 6-digit code into the
env; it would only work for ~30 seconds.

---

## 4. Authentication

Zerodha's official auth flow is interactive:

```
User clicks login URL → Zerodha login form (in their browser) →
  user types password + TOTP → Zerodha redirects to your registered
  Redirect URL with ?request_token=… → your server exchanges that token
  + your API secret for an access_token (good until 06:00 IST next day).
```

For an autonomous agent you have two choices:

- **A. Scripted (recommended for headless agents):** impersonate the
  browser. POST credentials + TOTP, then follow the OAuth redirect
  yourself. Requires `KITE_PASSWORD` + `KITE_TOTP_SECRET`. Self-contained,
  no human in the loop. **This guide focuses on this flow.**
- **B. Browser-based (recommended for tools used by a human):** redirect
  the human to the Kite login URL, let them log in, receive the
  `request_token` at your callback, exchange for `access_token`. Requires
  only `KITE_API_KEY` + `KITE_API_SECRET`. See
  <https://kite.trade/docs/connect/v3/user/#login-flow> for that path.

### 4.1 Scripted auth flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. POST https://kite.zerodha.com/api/login                               │
│      form data:  user_id, password                                       │
│      response:   JSON { data: { request_id: "..." } }                    │
│                                                                          │
│ 2. POST https://kite.zerodha.com/api/twofa                               │
│      form data:  user_id, request_id,                                    │
│                  twofa_value = pyotp.TOTP(seed).now(),                   │
│                  twofa_type  = "totp"                                    │
│      response:   JSON { status: "success" }   (cookie-authenticated)     │
│                                                                          │
│ 3. GET  https://kite.zerodha.com/connect/login?api_key=<KEY>&v=3         │
│      same Session as steps 1+2, allow_redirects=False                    │
│      response:   302 chain ending at <REDIRECT_URL>?request_token=…      │
│                  Pluck request_token from the query string.              │
│                                                                          │
│ 4. KiteConnect(api_key=KEY).generate_session(request_token, api_secret)  │
│      response:   { access_token: "...", ... }                            │
│                                                                          │
│ 5. KiteConnect(api_key=KEY).set_access_token(access_token)               │
│      ← this client is now live and authenticated until 06:00 IST next    │
│        day, after which Zerodha invalidates the token and you must       │
│        repeat steps 1–5.                                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Token caching (daily rotation)

**Every access_token expires at 06:00 IST every day**, regardless of when
it was issued. A naive script that re-authenticates on every call will burn
through Zerodha's rate limits and risk getting throttled. Cache it instead:

```
{
  "access_token":  "<opaque>",
  "expires_at":    "2026-06-25T06:00:00+05:30"
}
```

On startup: load the cache, check `datetime.now(timezone.utc) >= parse(expires_at)`
(tz-aware comparison — a naive `datetime.now()` running inside a UTC
container reads ~5h30m earlier than IST and lets revoked tokens look valid),
re-auth only if expired.

### 4.3 Mid-process re-authentication

A long-lived agent process holds the `KiteConnect` client in memory. When
the daily 06:00 IST rotation happens, every subsequent call fails with one
of:

- `"Incorrect api_key or access_token"` (the normal case)
- A Cloudflare HTML `"Just a moment..."` page (when Zerodha is under load)

Wrap every Kite call in a small retry helper:

```python
def is_auth_error(exc):
    msg = str(exc).lower()
    return ("incorrect api_key" in msg
            or "access_token" in msg
            or "token is invalid" in msg
            or "just a moment" in msg)        # CF challenge

def call_with_retry(name, fn):
    try:
        return fn()
    except Exception as e:
        if not is_auth_error(e):
            raise
        # Re-auth: delete cache + run the whole flow again
        new_kite = login()                    # the function from Section 5
        globals()["kite"] = new_kite          # or rebind however your agent stores it
        return fn()                           # one retry
```

Skipping the wrap on *one* call site is the single most common cause of a
production agent silently going blind around 06:00 IST.

---

## 5. End-to-end runnable example

A complete script. Reads env vars → authenticates → fetches live data for
three stocks → prints structured output. Copy-paste into any Python 3.9+
environment with `kiteconnect`, `pyotp`, and `requests` installed:

```python
#!/usr/bin/env python3
"""Minimal Kite Connect live-data client. No wrappers — every step inline
so you can see exactly what's happening."""

import os, datetime as dt
import pyotp, requests
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect

# ── 1. Credentials from env ─────────────────────────────────────────────
API_KEY     = os.environ["KITE_API_KEY"]
API_SECRET  = os.environ["KITE_API_SECRET"]
USER_ID     = os.environ["KITE_USER_ID"]
PASSWORD    = os.environ["KITE_PASSWORD"]
TOTP_SECRET = os.environ["KITE_TOTP_SECRET"]    # base32 seed, NOT the 6-digit code


def login() -> KiteConnect:
    """Run the full scripted auth flow and return an authenticated client."""
    s = requests.Session()

    # Step A — password login
    r = s.post("https://kite.zerodha.com/api/login",
               data={"user_id": USER_ID, "password": PASSWORD})
    r.raise_for_status()
    request_id = r.json()["data"]["request_id"]

    # Step B — TOTP 2FA (re-derive 6-digit code from base32 seed)
    s.post("https://kite.zerodha.com/api/twofa", data={
        "user_id":    USER_ID,
        "request_id": request_id,
        "twofa_value": pyotp.TOTP(TOTP_SECRET).now(),
        "twofa_type": "totp",
    }).raise_for_status()

    # Step C — hit /connect/login on the same session and follow the 302
    # chain manually. The Location header of the final redirect contains
    # ?request_token=… (the existing Kite app is registered with `127.0.0.1`
    # as its Redirect URL — see Section 3.1 — so we watch for that host).
    resp = s.get(f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3",
                 allow_redirects=False)
    request_token = None
    for _ in range(10):
        if resp.status_code not in (301, 302):
            break
        loc = resp.headers.get("Location", "")
        if "127.0.0.1" in loc or "localhost" in loc:
            request_token = parse_qs(urlparse(loc).query).get("request_token", [None])[0]
            break
        resp = s.get(loc, allow_redirects=False)
    if not request_token:
        raise RuntimeError(
            "Could not extract request_token from redirect chain. "
            "This usually means credentials are wrong or the app's Redirect "
            "URL has changed (must contain '127.0.0.1' or 'localhost' for "
            "this headless flow). Report to whoever owns the Kite app."
        )

    # Step D — exchange request_token for access_token
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    kite.set_access_token(data["access_token"])
    return kite


# ── 2. Authenticate (in production: cache the access_token until 06:00
#       IST next day — Section 4.2 — and reuse it across runs) ─────────
kite = login()
print("PROFILE:", kite.profile()["user_id"], kite.profile()["broker"])

# ── 3. Live data ─────────────────────────────────────────────────────────
# Symbol format for Kite is 'EXCHANGE:TRADINGSYMBOL' (e.g. 'NSE:RELIANCE',
# 'NSE:M&M', 'NSE:BAJAJ-AUTO'). It is NOT the yfinance-style 'RELIANCE.NS' —
# Kite will reject that.
symbols = ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"]

# Full quote: LTP + today's OHLC + market depth + volumes
quote = kite.quote(symbols)
for sym in symbols:
    q = quote[sym]
    print(f"{sym}: last={q['last_price']}  ohlc={q['ohlc']}  vol={q['volume_traded']}")

# Lightweight LTP-only (cheaper, use this if you only need last prices)
print("LTP:", kite.ltp(symbols))

# Today's OHLC (without depth)
print("OHLC:", kite.ohlc(symbols))

# Historical candles. `instrument_token` is the int returned by quote/ltp/ohlc
# or kite.instruments(). Interval is one of:
#   minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day.
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

Example output (during market hours; values change):

```
PROFILE: XX1234 ZERODHA
NSE:RELIANCE: last=1300.5  ohlc={'open': 1295.0, 'high': 1308.4, 'low': 1290.1, 'close': 1298.7}  vol=4523119
NSE:TCS:      last=2080    ohlc={...}  vol=987410
NSE:INFY:     last=1047.5  ohlc={...}  vol=...
LTP:  {'NSE:RELIANCE': {'instrument_token': 738561, 'last_price': 1300.5}, ...}
OHLC: {...}
RELIANCE daily candles (last 3 of 4):
   {'date': datetime.datetime(2026, 6, 20, 0, 0, tzinfo=<...IST>), 'open': 1291.0, 'high': 1303.5, 'low': 1288.0, 'close': 1298.7, 'volume': 4120445}
   ...
```

---

## 6. Symbol conventions

| Where | Format | Example |
|---|---|---|
| `kite.quote`, `kite.ltp`, `kite.ohlc` keys | `<EXCHANGE>:<TRADINGSYMBOL>` | `NSE:RELIANCE`, `NSE:M&M`, `BSE:RELIANCE` |
| `kite.historical_data` | `instrument_token` (int) | `738561` |
| `kite.instruments(exchange)` rows | `tradingsymbol` (no exchange prefix) | `RELIANCE` |

Common exchanges: `NSE`, `BSE`, `NFO` (futures + options), `MCX`
(commodities), `CDS` (currency derivatives).

**Do not** use yfinance-style `RELIANCE.NS` — Kite will reject it.

---

## 7. Common operations

### Get a list of instrument tokens

`kite.instruments("NSE")` returns ~10k rows, each a dict like:

```python
{
  'instrument_token': 738561,
  'exchange_token':   2885,
  'tradingsymbol':    'RELIANCE',
  'name':             'RELIANCE INDUSTRIES',
  'last_price':       0.0,
  'expiry':           '',
  'strike':           0.0,
  'tick_size':        0.05,
  'lot_size':         1,
  'instrument_type':  'EQ',     # EQ | FUT | CE | PE | …
  'segment':          'NSE',
  'exchange':         'NSE',
}
```

Heavy call (~MB of JSON). Cache the result to disk and refresh weekly. The
common pattern when scanning a universe: pre-load `instruments("NSE")`,
filter to `instrument_type == 'EQ'`, drop indices/ETFs/SME-suffixed
tickers, build a `tradingsymbol → instrument_token` map.

### Live quotes in batches

`kite.quote([...])`, `kite.ltp([...])`, `kite.ohlc([...])` accept up to
**500 symbols per call**. Split larger universes into batches with a 1s
sleep between batches (see rate limits below).

### Intraday minute candles

```python
candles = kite.historical_data(
    instrument_token,
    from_date = dt.datetime(2026, 6, 20,  9, 15),
    to_date   = dt.datetime(2026, 6, 20, 15, 30),
    interval  = "minute",
)
```

Intraday intervals (`minute`..`60minute`) carry back ~60 days. `day`
candles carry back ~years. Returned `date` fields are **tz-aware IST**
(`+05:30`); strip the tz only if you have a deliberate reason.

### Order placement (paper for now? real later?)

```python
order_id = kite.place_order(
    variety           = kite.VARIETY_REGULAR,
    exchange          = kite.EXCHANGE_NSE,
    tradingsymbol     = "RELIANCE",
    transaction_type  = kite.TRANSACTION_TYPE_BUY,
    quantity          = 1,
    order_type        = kite.ORDER_TYPE_MARKET,
    product           = kite.PRODUCT_CNC,
)
```

Order placement consumes funds. Get it right before pointing it at a real
account.

---

## 8. Rate limits

Zerodha's published per-app limits:

| Endpoint | Limit |
|---|---|
| `quote`, `ohlc`, `ltp` | 1 req/s (each request can carry up to 500 symbols) |
| `historical_data` | 3 req/s |
| `orders` / order modifications | 10 req/s |
| Overall | 10 req/s |

A typical universe scan that pulls OHLC for 3000 stocks: 6 batches × 500
symbols × 1s sleep ≈ 6 seconds, well under any limit. Burst above the
limit and you'll get HTTP 429s plus the occasional Cloudflare interstitial.

---

## 9. Gotchas

1. **TOTP seed vs TOTP code.** `KITE_TOTP_SECRET` is the long **base32 seed**
   from the QR-code/setup screen, *not* the 6-digit code an authenticator
   app shows. The code re-derives the 6-digit value per login.
2. **Daily token rotation.** Access tokens die at 06:00 IST every day. Long-
   running processes must detect the resulting auth error and re-login —
   see Section 4.3. Missing the wrap on even one call site silently breaks
   the agent at the first rotation after deployment.
3. **Naive datetime in expiry checks.** Cache-expiry comparisons must be
   tz-aware. A naive `datetime.now()` running inside a UTC container reads
   5h30m behind IST and treats revoked tokens as still valid.
4. **Redirect URL on the app.** The scripted flow extracts `request_token`
   from any redirect whose host contains `127.0.0.1` or `localhost`. The
   existing app is registered with `127.0.0.1`. If that ever changes, this
   flow stops working — flag it to the app owner.
5. **Subscription billing.** Kite Connect is ₹500/month and Zerodha
   disables the app the moment billing fails. Symptom: login + 2FA both
   succeed, but `/connect/login` returns `Invalid api_key`. Fix is in the
   developer console, not in code.
6. **Cloudflare challenges.** Under load, individual responses may come
   back as an HTML `"Just a moment..."` page instead of JSON. Treat as an
   auth-style error: one re-auth retry, then back off — do not hammer.
7. **`kite.instruments(exchange)` is heavy.** Cache it. Refreshing daily
   is fine; refreshing on every call is abusive.
8. **Symbol format.** Always `EXCHANGE:TRADINGSYMBOL` (e.g. `NSE:M&M`,
   `NSE:BAJAJ-AUTO`). `.NS` is yfinance, not Kite.
9. **Weekends / holidays.** Quotes still respond (last traded values).
   `historical_data` for intraday intervals returns empty arrays for non-
   trading days. Skip them explicitly.
10. **Don't put the secret in source.** It is genuinely worth the same
    care as a database password.

---

## 10. Debug runbook

| Symptom | Likely cause | Fix |
|---|---|---|
| `Invalid api_key` from `/connect/login` after login + 2FA succeed | Kite Connect subscription on the app has lapsed, or the app was disabled | Not fixable from here — report to the app owner so they can reactivate the subscription. |
| `Failed to extract request_token from redirect chain` | App's Redirect URL doesn't contain `127.0.0.1`/`localhost`, OR Zerodha returned 400 on `/connect/login` (see above) | Update Redirect URL on the app page, or check the actual response body |
| `Incorrect api_key or access_token` partway through a long run | Daily 06:00 IST token rotation; in-memory client is stale | Implement Section 4.3 retry; ensure *every* Kite call goes through it |
| TOTP step keeps failing | Wrong env value (6-digit code instead of base32 seed) or server clock skew vs IST | Verify env holds the long base32 string; check system time vs `time.nist.gov` |
| `historical_data` returns `[]` for an intraday interval | Outside the ~60-day window, weekend/holiday, or symbol untradable | Try `day` interval; verify the symbol is in `kite.instruments(...)` |
| HTML `"Just a moment..."` body where JSON expected | Cloudflare challenge under load | One re-auth retry, then back off; don't hammer |

A 4-line smoke test that proves the integration is healthy:

```python
from kiteconnect import KiteConnect
kite = login()              # the function from Section 5
print(kite.profile())
print(kite.ltp(["NSE:RELIANCE"]))
```

If both lines print real data, you're good. If `profile()` raises
`Invalid api_key`, you're on the subscription path.

---

## 11. References

- Python SDK source: <https://github.com/zerodhatech/pykiteconnect>
- HTTP API reference: <https://kite.trade/docs/connect/v3/>
- Developer console (apps, key/secret, Redirect URL, subscription):
  <https://developers.kite.trade/apps/>
- Forum (good for symptom searches): <https://kite.trade/forum/>
- Zerodha status page: <https://status.zerodha.com/>
