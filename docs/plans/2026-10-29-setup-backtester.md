# Setup backtester (step 2 of the setup plan)

Goal: score codified intraday setups on years of history in minutes, so the
"does this setup have an edge?" question no longer waits on ~1 live trade a day.
Opus proposes setups (step 3); this scores them; proven ones go to cheaper
models (step 4).

## Where it runs

| Part | Where | When | Why |
|---|---|---|---|
| Fetch candles | VM, inside the container | after 15:30 / before 09:15 / weekends | Kite creds live only on the VM. Historical API is ~3 req/s per key and the live agents use it every cycle, so a bulk fetch in market hours would starve the real-money session. The script refuses to run 09:00-15:40 IST unless `--force`. |
| Backtests | Mac | any time | VM has 2 CPUs / ~830 MB free; a memory spike can OOM-kill agents silently (CLAUDE.md). Candle files aren't secret: rsync VM -> Mac only. Never copy .env or tokens. |

## Data

- 5-minute candles, Nifty 200 constituents (today's list: survivorship bias accepted for v1)
  plus NIFTY 50, NIFTY BANK, INDIA VIX. ~2 years.
- Kite returns at most 100 days per 5-minute request (verified on the first run, chunk size is a constant).
- Size: 200 x ~8 requests ~ 1,600 requests x 0.4 s ~ 15-20 min. ~75 bars x ~500 days x 200 ~ 7.5M rows, ~150 MB csv.gz.
- Stored at /app/data/candles/5minute/<SYMBOL>.csv.gz (persistent volume), resumable (only missing ranges are fetched).

## Engine rules

- Signals use completed bars only; entry at the next bar's open (+/- slippage).
- Daily features (ATR14, 20-day volume, previous-day turnover) from prior days only.
- Stop and target checked on each bar's high/low; if one bar touches both, the stop wins.
  A bar that opens through the stop fills at its open.
- Forced exit at the 15:15 bar's open. One trade per setup per stock per day. Entries 09:30-14:30.
- Costs: 0.05% slippage per side + trade_context.est_intraday_charges.
- Size: fixed notional (default Rs 10,000, live-like; Rs 1L paper-like is a flag).
- Out-of-sample: the last 6 months (fixed split date passed once and kept). Every trade is tagged `sample=in|out`.
- Each trade carries setup, direction and the same context fields as live entries
  (regime, time_bucket, liquidity, gap_pct, from_open_pct, vs_vwap_atr, atr_pct, rs_vs_nifty, vix),
  so trade_context.summarise() works on backtest and live trades alike.
  Difference: liquidity uses the previous day's full turnover (live uses today's so far).

## Setups v1 (exact rules, params as data)

- gap_fade: |gap| >= 2%. After the opening range (15 min), a bar closes back through VWAP
  against the gap direction and beyond the day's open. Stop: the day's extreme so far
  (at least 0.5 ATR away). Target: 1.5R.
- orb_breakout: a bar closes beyond the 15-minute opening range with bar volume >= 1.5x the
  day's average bar volume so far. Stop: other side of the range (at least 0.5 ATR). Target: 1.5R.
- vwap_cross: price held one side of VWAP for >= 6 bars, then a bar closes on the other side
  by >= 0.1 ATR. Stop: 0.5 ATR beyond VWAP. Target: 1.5R.

## Promotion bar (for step 4)

N >= 30 out-of-sample trades in a setup x regime bucket, positive net expectancy after costs,
and not carried by one outlier (net with the best trade removed still > 0).
