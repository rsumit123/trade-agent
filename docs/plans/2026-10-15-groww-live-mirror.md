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
