# Zerodha Kite Connect API — Integration Guide

How AlphaAgent talks to Zerodha's Kite Connect API for live NSE market data
(quotes, OHLC, intraday + historical candles, instrument master). Audience:
other agents/developers who need to consume the same live feed.

---

## ⚠️ Current status (as of 2026-06-24)

The integration is **OUT** — Zerodha is returning `Invalid api_key` on the
`/connect/login` step (Step 3 of the auto-login flow below). Login + 2FA still
succeed; the key itself is rejected at exchange time. Most likely cause: the
**₹500/month Kite Connect API subscription lapsed**. Fix:

1. Log into <https://developers.kite.trade/apps/>.
2. Open the AlphaAgent app (key prefix `bfta…`).
3. Renew the subscription / reactivate the app.
4. No code change required — the next agent cycle (or `auth.get_authenticated_client()`)
   will succeed automatically once the key is reactivated.

This doc otherwise describes the **working** integration; everything below
applies as soon as the key is live again.

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

The Python deps you need: `kiteconnect`, `pyotp`, `requests`. Already in our
`requirements.txt`.

---

## 3. Authentication flow (`agent/kite_auth.py`)

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
