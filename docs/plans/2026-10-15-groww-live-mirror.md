# nse_opus_live — real-money mirror on Groww (plan, 2026-10-15)

Goal: measure paper vs real on the SAME decisions. A new session `nse_opus_live`
(clone of nse_opus: Opus, nse-intraday preset, scanner) with ₹50,000 paper capital.
Every paper event is mirrored to a real Groww order at the same quantity, automatically,
with no per-order approval (the user's explicit instruction). Data stays on Kite; only orders go to Groww.

## Settled facts (researched 2026-10-15)
- Groww auth: official TOTP flow, "No Expiry", no daily approval:
  `GrowwAPI.get_access_token(api_key=KEY, totp=pyotp.TOTP(SECRET).now())`. Limit: 150 token calls/day,
  so cache the token in sessions/_groww_token.json for the day.
- SDK `growwapi` 1.5.0: PRODUCT_MIS, ORDER_TYPE_LIMIT/MARKET/STOP_LOSS/STOP_LOSS_MARKET,
  VALIDITY_DAY, SEGMENT_CASH, EXCHANGE_NSE; place/modify/cancel_order, get_order_status,
  get_trade_list_for_order, get_positions_for_user, get_available_margin_details. No market_protection
  parameter, so whether a MARKET order works on cash MIS is unverified until the smoke test.
- Rate limits: orders 10/s, 250/min. Static IP must be whitelisted: VM egress = 34.139.217.34 (verified).
- Groww auto square-off: positions open at 15:20 get squared off, with a ₹50 + GST charge per position.
  Our forced close is 15:15, so exits must complete within ~4 minutes.
- Charges (Groww help page, used as a model): brokerage min(₹20, 0.1%) per executed order
  (if under ₹5, then min(₹5, 2.5%)); STT 0.025% on sell; NSE exchange charge 0.00297% both sides;
  SEBI 0.0001%; stamp 0.003% on buy; IPFT 0.0001%; GST 18% on brokerage + exchange + SEBI.

## Design
1. **Hook at the portfolio layer.** A `LivePortfolio` wraps `Portfolio`, so every path is mirrored:
   execute_buy/short (entry), execute_sell/cover (LLM exit, 15-second stop sweep, forced close) and update_levels.
   Paper always books first and stays the record for the agent and the learner. The live side never
   raises into the agent loop.
2. **Entry:** a LIMIT MIS order at paper price ± a 0.3% buffer, rounded to the instrument tick (Kite
   instruments tick_size). Poll for up to 30s. If unfilled, cancel → `missed`. On a partial fill, keep the
   filled quantity. Later paper events for a missed trade do nothing on the live side.
3. **Broker-side protective stop:** after the entry fills, place a STOP_LOSS (SL-limit) order at the paper
   stop (trigger = stop, limit = stop ± 1%). It protects against a VM crash or preemption.
   On update_levels, modify it.
4. **Exit (no double-exit):** cancel the SL order → read its status. If it filled, record that fill as the real
   exit. Otherwise, send the exit as an aggressive LIMIT at LTP ∓ 0.5%; re-price every 10s; after 3 tries use MARKET
   (if the smoke test showed MARKET works) or a further-widened limit. Never send an exit for more than the
   real filled quantity.
5. **Live ledger:** sessions/nse_opus_live/live_orders.jsonl (paper_trade_id, leg, groww_order_id,
   requested price/qty, fill price/qty, status, timestamps, error). It is not part of the trades.db schema.
6. **Reconcile:** on startup and every monitor tick, compare Groww positions with ledger-open legs. Any
   mismatch → Telegram alert. At 15:16, if Groww still shows open MIS quantity → escalate the exit.
7. **Guards (in code, with tests):**
   - live only if session_id == "nse_opus_live" AND it's in env GROWW_LIVE_SESSIONS AND config live_broker == groww
   - kill-switch file sessions/nse_opus_live/LIVE_KILL → no new real entries (exits still go out)
   - total open real exposure ≤ ₹50,000; per-order value ≤ ₹50,000
   - daily real realized loss ≤ −₹1,500 → no new real entries for the day
   - ≤ 40 real orders/day; no-op in backtest; MIS only; NSE EQ symbols only
   - any auth or static-IP failure → mirror disabled for the day + Telegram (paper continues)
8. **Smoke test at 09:16 (automatic):** a 1-share BUY of a liquid ~₹100–400 stock using the entry path,
   then SELL using the exit path; it also tests MARKET acceptance. Cost ≈ ₹10–15. If it fails → mirror off for the day + alert.
9. **Reporting:** Telegram on every real fill/miss/reject. EOD report: per trade, paper price vs real fill
   (slippage bps), modelled charges, misses/rejects, paper P&L vs real net P&L.
10. **No live-only liquidity filter.** It would make the real trades different from paper. Groww rejections
    (MIS-blocked, T2T, ASM) are recorded as data.

## Sequence
- After the 16:07 auto-fix deploy is verified: build + tests locally (FakeGroww).
- When GROWW_API_KEY / GROWW_TOTP_SECRET are in the VM .env: a read-only live check (token, margin, positions).
  Without these by ~21:00 IST, nse_opus_live runs paper-only tomorrow.
- Deploy after the close/evening; create nse_opus_live; schedule the 09:16 smoke test and a 09:45 check.
- Capacity: MAX_RUNNING_AGENTS = 8 (auto-restart already runs 9); VM has ~850MB free. A 10th agent needs
  a slot: ask the user (pause a BTC experiment, or stop open-webui 522MB).

## Rework after the adversarial review (as built)
- All broker calls run on one worker thread (LiveWorker). Paper books on the agent thread and enqueues.
- Leg states: pending → entry_working/unknown → open → exiting → closed | stuck; missed/rejected/blocked/skipped.
  Nothing is recorded as missed/rejected without a final broker state.
- Every order has a deterministic order_reference_id (YYMMDD-<trade>-<kind><n>). An ambiguous reply is resolved
  by reference lookup, never by retrying blind.
- Protection: OCO (target MARKET, stop SL_M; triggers modified on every update_levels). Falls back to a plain
  SL if the OCO fails. Groww has no trailing stop, so Opus trails through update_levels.
- Exit: cancel protection → final → resolve earlier exit orders → read the net position → exit capped at
  min(owned, net). Retried every tick until flat; at 15:19 status is "stuck" and an alert goes out.
- Eligibility comes from Groww's instrument CSV (is_intraday, buy/sell_allowed, tick_size).
  live_universe_filter: mis_only filters the scanner picks.
- nse_opus_live config.yaml must set: live_broker: groww, live_universe_filter: mis_only,
  intraday_close_buffer_min: 20 (forced close at 15:10), starting_capital: 50000, and
  GROWW_LIVE_SESSIONS=nse_opus_live in the VM .env.

## Operator runbook

### Setup
`sessions/nse_opus_live/config.yaml` (a clone of nse_opus, plus):
```yaml
session_id: nse_opus_live
market: nse-intraday
data_source: kite
llm_provider: anthropic          # same model as nse_opus
starting_capital: 50000
live_broker: groww               # gate 3 of 3
live_universe_filter: mis_only   # trade only stocks Groww allows intraday
intraday_close_buffer_min: 20    # paper and real close at 15:10 (Groww squares off at 15:20)
```
VM `.env` (never in git; the user adds these themselves):
- `GROWW_API_KEY`, `GROWW_TOTP_SECRET`: a Groww Cloud **TOTP** API key (no daily approval).
- `GROWW_LIVE_SESSIONS=nse_opus_live`: gate 2. Remove it to stop real entries; positions still open
  today are then managed in exit-only mode.
- `GROWW_SMOKE_SYMBOL=ITC.NS` (optional): the 1-share stock for the daily smoke test.
- `GROWW_FORCE_PLAIN_SL=1` (optional): never use OCOs; protect with plain SL orders only.
Gate 1 is hard-coded: only `nse_opus_live` can ever trade real money.
Static IP `34.139.217.34` must be whitelisted in Groww Cloud. Env changes need a container restart.

### Daily flow
- The first tick after 09:15 runs the smoke test (about ₹10–15 in charges). It buys 1 share, checks the
  API shapes, places, modifies and cancels an OCO, and sells. No real entries happen before it passes.
- Paper trades are mirrored automatically. The forced close is at 15:10. A stuck exit raises an
  alert at 15:19.
- The EOD paper-vs-real report goes to Telegram after the daily review. The ledger is
  `sessions/nse_opus_live/live_orders.jsonl`.

### Kill switch (stop new real entries, keep managing open ones)
`ssh ssh-social "docker exec alphaagent-backend touch /app/sessions/nse_opus_live/LIVE_KILL"`
To undo: `rm` the same file. Paper trading continues either way.

### Liquidate (flatten everything now)
Use the dashboard **Liquidate** button for nse_opus_live. It writes a dated `LIVE_LIQUIDATE` request and
stops the agent. The agent flattens real positions (≤60s) before it exits. The dashboard then waits
for the live lock and finishes the job in the background until Groww is flat. New real entries stay
off for the rest of the day.
If the dashboard is unavailable: write today's date into `/app/sessions/nse_opus_live/LIVE_LIQUIDATE`
(e.g. `date +%F > …`). The running agent acts on it at its next tick. A file dated another day is ignored.
Last resort: square off in the Groww app. The mirror reconciles and alerts on any mismatch.

### Stop / restart
A plain stop (SIGTERM) lets in-flight orders settle. It then makes sure every open real position has a
protective order at Groww, capped at what Groww shows, before the process exits. On restart the ledger
is replayed and any leg whose paper trade closed is exited.

### Telegram alerts ([nse_opus_live LIVE] …) and what to do
| Alert | Meaning | Action |
|---|---|---|
| Smoke test passed … Real orders are ON | Daily check OK; says whether MARKET orders and OCOs are in use | none |
| Real ENTRIES DISABLED for today: … | Smoke test failed, auth/IP failure, 3 broker errors in a row, untrusted positions, or liquidation | Read the reason. Auth/IP: check keys and the IP whitelist. Exits keep working |
| OCOs OFF for today (plain stops) | The smoke test couldn't verify the OCO API shapes | none; plain SL protection is used |
| Blocked real … not_intraday_allowed / buy_not_allowed / sell_not_allowed / eligibility_unknown | Groww doesn't allow that stock intraday (or the list couldn't load) | none; paper trade only |
| Skipped real … (kill switch / exposure / daily real loss / order cap / smoke test / exit-only) | A guard stopped the entry | Expected; review if frequent |
| Blocked real … manual_position_conflict | You have your own MIS orders or position in that stock today; the mirror stays out of it | none (close yours if you want the mirror to trade it) |
| Skipped real … > available ₹… (MIS balance ₹…) | The order is larger than Groww's mis_balance_available (or the ₹50K cap left) | Add funds, or ignore |
| the OCO's own order is still working … | A triggered or cancelled OCO's leg order (MISOCO_*) hasn't finished. The mirror never cancels Groww's OCO legs and won't send an exit next to one | Check the app; it usually clears by itself |
| … OCOs match ours — can't tell which | An OCO create reply was lost and more than one OCO with the same symbol, qty and triggers exists | Cancel the extra OCO in the app |
| Real BUY/SELL … filled @ … / MISSED / REJECTED [category] | Entry outcome; the category and raw Groww message are in the ledger and EOD report | Check circuit/margin rejections |
| Can't read the real … order — status UNKNOWN | A status read failed; it retries every tick | Only act if it persists for minutes |
| OCO/Protective stop … may or may not exist | A protective order's reply was lost; the leg can't close until it's found | Check the Groww app for an extra OCO/SL on that stock |
| Position fields for X disagree / positions say A but fills say B | Groww positions and order fills disagree. After 15s the leg goes stuck and is re-protected (capped), and entries stop | Check the Groww app position; square off manually if needed |
| exit order … unresolved for 30s — re-protecting | An exit's reply was lost and can't be resolved | Check the Groww app |
| X is at its lower/upper circuit — can't exit | Stuck at a circuit limit | Watch it; nothing can trade until it opens |
| STILL OPEN at 15:19 / real exit not confirmed by 15:19 | Not flat before Groww's 15:20 square-off (₹50+GST per position) | Square off in the Groww app now |
| Position mismatch X: Groww +a, ledger expects +b | Reconcile found a difference (a manual trade, or a missed fill) | Check the app; manual trades in the same stock are expected |
| Cancelled a leftover protective stop/OCO | The orphan sweep removed one of our orders on a flat stock | none |
| Agent stopping. Real positions: … | SIGTERM summary | Confirm protection in the app if anything is listed |
| Liquidation incomplete / couldn't get the live lock | The flatten didn't finish | Square off in the Groww app |

### Force plain stops
Set `GROWW_FORCE_PLAIN_SL=1` in the VM `.env` and restart. The smoke test then skips the OCO steps and all
protection uses plain SL orders.

### Sharing the account with manual trades
The mirror only owns orders carrying its own reference prefix (`YYMMDD-…`), plus the MISOCO_* leg
orders of OCOs it created. Those are matched by symbol, side, quantity and time, because Groww's OCOs have
no reference id and their leg refs are random. Everything else is yours: your orders, OCOs and positions
are never cancelled or squared off by the mirror. Its position math subtracts your fills. It won't enter
a stock where you have MIS activity that day.
