# AI Trading Agent — Architecture & Flow Documentation

> Auto-generated documentation of the complete system flow.
> Last updated: 2026-04-02

---

## Table of Contents

1. [Overview](#1-overview)
2. [Project Structure](#2-project-structure)
3. [Startup Flow](#3-startup-flow)
4. [Single Cycle — run_once()](#4-single-cycle--run_once)
5. [Decision Engine — LLM Tool Loop](#5-decision-engine--llm-tool-loop)
6. [Tool Handler — place_trade Detail](#6-tool-handler--place_trade-detail)
7. [Portfolio Management](#7-portfolio-management)
8. [Risk Manager](#8-risk-manager)
9. [Learner & Journal](#9-learner--journal)
10. [Market Data & Indicators](#10-market-data--indicators)
11. [Web Research](#11-web-research)
12. [Daily Review Flow](#12-daily-review-flow)
13. [Dashboard API](#13-dashboard-api)
14. [Configuration Reference](#14-configuration-reference)
15. [Full Cycle Sequence Diagram](#15-full-cycle-sequence-diagram)

---

## 1. Overview

An autonomous LLM-driven paper trading agent for Indian stock markets (NSE).
Runs a continuous **Observe → Decide → Act → Learn** loop every 15 minutes during market hours.

**Key design choices:**
- LLM (Claude Haiku via OpenRouter) makes all trade decisions using tool calls
- All state is persisted in SQLite (`data/trades.db`)
- Hard risk limits enforced in code — the LLM cannot bypass them
- Learning journal (`learnings/journal.md`) feeds prior lessons back into every cycle
- Both LONG (BUY/SELL) and SHORT (SHORT/COVER) positions supported, shorts are intraday-only

---

## 2. Project Structure

```
ai-trader/
├── run.py                    # Entry point, argument parsing, PID lock
├── agent/
│   ├── __init__.py           # Exports TradingAgent, AgentConfig
│   ├── config.py             # AgentConfig dataclass — all settings
│   ├── runner.py             # TradingAgent — orchestrates the full cycle
│   ├── decision_engine.py    # LLM brain — Anthropic + OpenRouter engines
│   ├── market_data.py        # yfinance wrapper — prices, RSI, ATR, VWAP
│   ├── portfolio.py          # SQLite paper trading engine
│   ├── risk_manager.py       # Hard limits, stop/target sweep
│   ├── learner.py            # Journal writes, reflections, distilled rules
│   └── web_research.py       # DuckDuckGo search + tool definitions
├── dashboard/
│   ├── app.py                # FastAPI backend
│   └── index.html            # Single-page frontend
├── data/
│   ├── trades.db             # SQLite database (portfolio + trades)
│   └── agent.lock            # PID lock file (prevents duplicate processes)
├── learnings/
│   └── journal.md            # Learning journal (trade logs + reflections)
└── logs/
    └── agent.log             # Full agent activity log
```

---

## 3. Startup Flow

```
run.py:main()
│
├── Parse CLI arguments
│     --loop             → run continuously (intraday mode)
│     --swing            → 30-min cycle instead of 15-min
│     --review           → run daily review and exit
│     --status           → print portfolio status and exit
│     --force-intraday   → hint LLM to prefer intraday trades
│     --provider         → override LLM provider
│     --capital          → override starting capital
│
├── IF --loop: acquire PID lock (prevents multiple simultaneous instances)
│     └── Write PID to data/agent.lock
│     └── If lock exists with live PID → exit immediately
│     └── Register atexit cleanup
│
└── TradingAgent.__init__(config)
      ├── Create directories: data/, learnings/, logs/
      ├── Setup logging (FileHandler → logs/agent.log + force=True)
      │
      └── Initialize 6 core components:
            ├── Portfolio(db_path, starting_capital)
            │     └── SQLite init + schema migrations
            ├── MarketData(watchlist)
            │     └── 111-stock watchlist loaded
            ├── WebResearcher()
            │     └── DuckDuckGo search + 90-min cache
            ├── RiskManager(config, portfolio)
            │     └── Hard limit enforcer
            ├── Learner(config, portfolio)
            │     └── Journal file created if not exists
            └── DecisionEngine = create_engine(config)
                  ├── IF provider="anthropic" → AnthropicDecisionEngine
                  └── IF provider="openrouter"/"openai" → OpenRouterDecisionEngine
```

---

## 4. Single Cycle — run_once()

Called every 15 minutes during market hours. Full sequence:

```
TradingAgent.run_once(force_intraday=False)
│
├── PHASE 1 — OBSERVE (gather all market data)
│     │
│     ├── market_data.get_current_prices()
│     │     └── Fetch 1m intraday candles for all watchlist tickers
│     │         (5-min cache to avoid hammering yfinance)
│     │
│     ├── market_data.get_watchlist_summary()
│     │     └── For each of 111 stocks:
│     │           └── get_stock_context(ticker, include_vwap=False)
│     │                 ├── 30-day daily OHLCV from yfinance
│     │                 ├── RSI(14)  — Wilder smoothing
│     │                 ├── ATR(14)  — True Range exponential smoothing
│     │                 ├── SMA(5)
│     │                 ├── Distance to 5-day high (resistance)
│     │                 ├── Distance to 5-day low (support)
│     │                 └── Volume ratio (today vs 20-day average)
│     │
│     └── researcher.build_research_context(top_5_tickers)
│           ├── Broad market: search("India Nifty Sensex today {date}")
│           ├── Per ticker: get_news_for_stock(ticker)
│           │     ├── DuckDuckGo search with 90-min cache
│           │     └── Fallback: yfinance.Ticker.news
│           └── Format as text block for LLM context
│
├── PHASE 2 — CHECK RISK STATUS
│     └── risk_manager.get_risk_status(prices)
│           ├── Calculate today's realized P&L
│           ├── Check daily loss limit breach
│           └── Return: {daily_pnl, can_trade, open_positions, cash_available}
│
│     IF can_trade = False:
│       └── RETURN early — "blocked: daily loss limit hit"
│
├── PHASE 3 — STOP/TARGET SWEEP
│     └── risk_manager.run_stop_loss_sweep(prices)
│           └── For each open position:
│                 ├── Has ATR-derived stop/target? (new positions)
│                 │     ├── LONG: sell if price ≤ stop OR price ≥ target
│                 │     └── SHORT: cover if price ≥ stop OR price ≤ target
│                 └── Legacy position (no stored levels)?
│                       └── LONG: sell if loss > per_trade_loss_limit_pct (1%)
│
│           For each auto-closed trade:
│             └── learner.write_trade_log(trade, llm_client)
│                   └── LLM generates reflection → append to journal
│
├── PHASE 4 — INTRADAY CLOSE GUARD (≥ 15:15 IST)
│     └── risk_manager.check_intraday_close()
│           IF True:
│             ├── Force-close all open intraday LONG positions
│             │     └── portfolio.execute_sell(id, price, "End-of-day forced close")
│             ├── Force-cover all open intraday SHORT positions
│             │     └── portfolio.execute_cover(id, price, "End-of-day forced cover")
│             └── RETURN early — no new trades after 15:15
│
└── PHASE 5 — DECIDE + ACT (LLM decision loop)
      └── engine.run_decision_loop(
              portfolio_summary,    ← cash, holdings, P&L, open positions
              watchlist_data,       ← 111 stocks with all indicators
              news_context,         ← market + stock-specific news
              risk_status,          ← limits, daily P&L
              learnings,            ← distilled rules + recent journal entries (6000 chars)
              tool_handler,         ← callback: handle_tool_call()
              is_market_open,
              force_intraday
            )
            └── See Section 5 for full detail
```

---

## 5. Decision Engine — LLM Tool Loop

The LLM receives all market context and iterates using tool calls until done (max 10 iterations).

### System Prompt Contents

```
TRADING_SYSTEM_PROMPT includes:
  ├── Role: autonomous AI trading agent on NSE
  ├── Trading Rules:
  │     ├── Intraday must close before 3:15 PM IST
  │     ├── Swing max 1–5 days
  │     ├── Shorts are ALWAYS intraday
  │     └── Cash is a position — don't force trades
  ├── Long vs Short guidance:
  │     ├── BUY when expecting rise
  │     ├── SHORT when expecting fall (RSI >70, near resistance, bad news)
  │     └── Short stop ABOVE entry, target BELOW entry
  ├── 4 available tools (see below)
  ├── Decision framework (sentiment → portfolio → scan → research → trade)
  ├── Risk awareness (limits are hard, accept rejections)
  └── Past Learnings: {journal distilled rules + recent entries}
```

### User Message Contents (built each cycle)

```
## Current Portfolio
{cash, holdings_value, total_value, total_return, open_positions, holdings[]}

## Risk Status
{daily_pnl, can_trade, cash_available, max_trade_amount}

## Full Watchlist (111 stocks)
Ticker               Price     Chg%    RSI    vol    res    sup    sma
RELIANCE.NS          ₹2800.00  +1.50%  RSI=52 vol=1.1x res=+2.1% sup=-1.3% ₹2770
...sorted by biggest movers first...

Legend: RSI <30=oversold, >70=overbought | vol: today/20d ratio

## Recent Market News
  1. [source] Market headline...
  2. [source] Stock-specific news...

LONG signals: RSI <35, vol >1.5x, near support
SHORT signals: RSI >70, near resistance, negative news
...

Analyze and decide on trades.
```

### Tool Definitions

| Tool | Inputs | Purpose |
|------|--------|---------|
| `search_market_news` | query, max_results | DuckDuckGo search for news |
| `get_portfolio_status` | (none) | Current cash, holdings, P&L |
| `get_stock_details` | ticker | Full technicals + VWAP for one stock |
| `place_trade` | action, ticker, quantity, trade_type, trade_id, reason | Execute BUY/SELL/SHORT/COVER |

### Loop Iteration

```
messages = [{"role": "user", "content": context_message}]

FOR i in range(10):
  │
  ├── Call LLM API with system + tools + messages
  │
  ├── IF response has no tool calls:
  │     └── LLM is done — log reasoning text — BREAK
  │
  └── ELSE for each tool_use in response:
        ├── result = handle_tool_call(tool_name, tool_input)
        ├── IF place_trade → track in actions_taken
        └── Append tool_result to messages

RETURN actions_taken
```

---

## 6. Tool Handler — place_trade Detail

All four trade actions handled in `runner.py:handle_tool_call()`.

### Hard guard (applied to BUY and SHORT)
```
IF check_intraday_close() → reject with "Cannot open new positions after 15:15 IST"
```

### BUY flow
```
place_trade(action="BUY", ticker, quantity, trade_type, reason)
  │
  ├── Get current price from market_data
  ├── risk_manager.check_buy(ticker, qty, price, prices)
  │     ├── Cash ≥ qty × price?
  │     ├── qty × price ≤ max_trade_amount (₹2L)?
  │     ├── Position % ≤ max_position_pct (20%)?
  │     ├── open_positions < max_open_positions (5)?
  │     └── today_pnl > -daily_loss_limit?
  │
  ├── IF rejected → return {success: False, error: reason}
  │
  ├── Compute ATR-based stop/target:
  │     ├── get_stock_context(ticker) → atr_14
  │     ├── stop_price   = entry − 1.5 × ATR
  │     └── target_price = entry + 2.0 × ATR  (1:2 risk/reward)
  │     └── Fallback: use config per_trade_loss_limit_pct %
  │
  ├── portfolio.execute_buy(ticker, qty, price, trade_type, reason, stop, target)
  │     ├── Debit cash (qty × price)
  │     ├── INSERT trades row (direction="long")
  │     └── Return Trade object
  │
  ├── learner.write_trade_log(trade)         ← append ENTRY to journal
  │
  └── Return {success: True, trade_id, price, stop_price, target_price, atr_14}
```

### SELL flow
```
place_trade(action="SELL", trade_id, reason)
  │
  ├── Verify open position exists (check_sell)
  ├── Get current price
  ├── portfolio.execute_sell(trade_id, price, reason)
  │     ├── pnl = round((price − entry) × qty, 2)
  │     ├── Credit proceeds (qty × price) to cash
  │     ├── UPDATE trade: status=closed, exit_price, pnl
  │     └── Return Trade object
  │
  ├── learner.write_trade_log(trade, llm_client)
  │     └── LLM generates 3–4 bullet reflection → append EXIT to journal
  │
  └── Return {success: True, exit_price, pnl}
```

### SHORT flow
```
place_trade(action="SHORT", ticker, quantity, reason)
  │
  ├── Same risk checks as BUY
  ├── ATR-based stop/target (REVERSED for shorts):
  │     ├── stop_price   = entry + 1.5 × ATR  ← price rising HURTS us
  │     └── target_price = entry − 2.0 × ATR  ← price falling HELPS us
  │
  ├── portfolio.execute_short(ticker, qty, price, reason, stop, target)
  │     ├── Credit short proceeds to cash  (liability — paid back on cover)
  │     ├── INSERT trades row (direction="short", action="SHORT")
  │     └── Return Trade object
  │
  ├── learner.write_trade_log(trade)         ← append SHORT entry to journal
  │
  └── Return {success: True, trade_id, price, stop_price, target_price}
```

### COVER flow
```
place_trade(action="COVER", trade_id, reason)
  │
  ├── Verify open SHORT exists
  ├── Get current price
  ├── portfolio.execute_cover(trade_id, price, reason)
  │     ├── pnl = round((entry − price) × qty, 2)  ← positive if price fell
  │     ├── Debit cover cost (qty × price) from cash
  │     ├── UPDATE trade: status=closed, exit_price, pnl
  │     └── Return Trade object
  │
  ├── learner.write_trade_log(trade, llm_client)
  │
  └── Return {success: True, exit_price, pnl}
```

---

## 7. Portfolio Management

All state lives in `data/trades.db` (SQLite).

### Database Schema

```sql
TABLE account (single row, id=1):
  cash              REAL     -- current balance (includes short proceeds)
  starting_capital  REAL     -- initial capital (₹10,00,000)
  created_at        TEXT

TABLE trades:
  id            INTEGER PRIMARY KEY AUTOINCREMENT
  ticker        TEXT
  action        TEXT     -- "BUY" or "SHORT"
  trade_type    TEXT     -- "intraday" or "swing"
  quantity      INTEGER
  entry_price   REAL
  entry_time    TEXT     -- ISO datetime
  exit_price    REAL     -- NULL if open
  exit_time     TEXT     -- NULL if open
  status        TEXT     -- "open" or "closed"
  pnl           REAL     -- NULL if open
  reason        TEXT     -- entry thesis
  exit_reason   TEXT     -- exit rationale
  stop_price    REAL     -- ATR-derived stop level
  target_price  REAL     -- ATR-derived target level
  direction     TEXT     -- "long" or "short"

TABLE daily_snapshots:
  date, cash, portfolio_value, total_value,
  daily_pnl, trades_taken, wins, losses
```

### Cash Accounting for Shorts

Short sale proceeds are **credited** to `account.cash` when a SHORT is opened
(to keep accounting consistent), but they are a **liability** to be repaid on COVER.

```
get_cash()     → raw_cash − sum(qty × entry_price for open shorts)
               = truly available cash for new buys/shorts

get_cash_raw() → raw account.cash (includes short proceeds)
               = used internally for accounting

total_value    = raw_cash − cover_cost_at_current + long_holdings_value
               = true net equity
```

### P&L Formulas

| Trade | P&L |
|-------|-----|
| LONG  | `(exit_price − entry_price) × quantity` |
| SHORT | `(entry_price − exit_price) × quantity` |

Both rounded to 2 decimal places.

---

## 8. Risk Manager

Hard limits — the LLM cannot bypass these.

### check_buy() — 6 checks in order

| # | Check | Limit |
|---|-------|-------|
| 1 | Sufficient cash | `trade_value ≤ get_cash()` |
| 2 | Max trade size | `trade_value ≤ ₹2,00,000` |
| 3 | Position % of portfolio | `≤ 20%` |
| 4 | Combined exposure to same ticker | `≤ 20%` |
| 5 | Max open positions | `< 5` |
| 6 | Daily loss limit | `today_pnl > −₹20,000 (2% of ₹10L)` |

### run_stop_loss_sweep() — called every cycle

```
For each open position:
  IF stop_price + target_price set (new positions with ATR levels):
    LONG:
      price ≤ stop_price  → execute_sell()   "Stop hit"
      price ≥ target_price → execute_sell()  "Target hit"
    SHORT:
      price ≥ stop_price  → execute_cover()  "Short stop hit"
      price ≤ target_price → execute_cover() "Short target hit"

  ELSE (legacy positions, no stored levels):
    LONG only:
      loss% > per_trade_loss_limit_pct → execute_sell()  "Stop-loss"
```

### check_intraday_close()
Returns `True` if current IST time ≥ 15:15.
Used to:
1. Force-close all intraday positions (longs + shorts)
2. Block any new BUY or SHORT in the same cycle
3. Skip the LLM decision loop entirely (return early from run_once)

---

## 9. Learner & Journal

Manages the feedback loop — trades are logged and learnings re-injected.

### Journal File Structure (`learnings/journal.md`)

```markdown
# 🧠 Trading Agent — Learning Journal

---

## 📌 Distilled Rules         ← Updated daily by update_distilled_rules()
                               ← ALWAYS included in get_learnings() context
- **HAVELLS RSI <30 + VWAP**: reliable intraday bounce...
- **Don't short after 14:45**: no time to recover...
...

---

## Initial Strategy Notes
...

### 2026-04-02 11:06 — Entry: SAPPHIRE.NS
**ENTRY** 80x SAPPHIRE.NS @ ₹145.26  [intraday]  [trade_id=21]
  *Thesis: Oversold RSI 29.1, volume spike...*

### 2026-04-02 12:43 — Exit: SAPPHIRE.NS
**EXIT** 80x SAPPHIRE.NS | WIN ✅ | ₹145.26 → ₹149.58 | P&L: ₹+345.60 | held 96m

• Thesis validated — RSI capitulation bounce worked...
• What worked: volume confirmation + VWAP support...
...
```

### write_trade_log() — called after every trade

```
ON ENTRY (exit_price is None):
  ├── Dedup check: if trade_id already in journal → skip
  └── Append: "ENTRY/SHORT {qty}x {ticker} @ ₹{price} [trade_id={id}]\n  Thesis: ..."

ON EXIT (exit_price set):
  ├── Compute hold time, P&L%
  ├── Call LLM (max 120 words, 3-4 bullets):
  │     "Did thesis play out? What went well/wrong? One concrete change next time?"
  └── Append: "EXIT ... | WIN/LOSS | entry → exit | P&L\n{reflection}"
```

### get_learnings() — called every cycle to feed LLM

```
Strategy: always include Distilled Rules + most recent entries within budget

max_chars = 6000 (set in runner.py)

├── Extract "## 📌 Distilled Rules" block
├── Budget: rules up to 3000 chars, recent entries up to remaining 3000
├── Truncate entries from end if needed (start from clean ### header)
└── Return: "{rules}\n\n---\n\n{recent_entries}"
```

### update_distilled_rules() — called end-of-day

```
├── Read full journal (strip old rules block)
├── IF journal < 500 chars → skip
├── Prompt LLM: "Synthesize ALL entries into top 20 specific rules"
│     Categories: setups that work, setups that fail,
│                 stop-loss lessons, timing rules, sector patterns
├── Insert "## 📌 Distilled Rules" block after journal header
└── Write back to journal.md
```

### generate_daily_review() — called end-of-day

```
├── Collect today's trades + portfolio summary
├── Prompt LLM: "Reflect on today's trades — 3-5 bullets"
│     1. What worked well and why
│     2. What didn't work + what you'd do differently
│     3. Patterns across recent trades
│     4. One specific rule to apply going forward
└── Append to journal as "## {date} — Daily Review"
```

---

## 10. Market Data & Indicators

All data from yfinance (free, no API key required).

### get_stock_context(ticker, include_vwap=True)

Fetches 30 days of daily OHLCV, computes:

| Indicator | Calculation |
|-----------|------------|
| `rsi_14` | Wilder smoothing: `ewm(com=13, adjust=False)` on daily gains/losses |
| `atr_14` | Wilder smoothing of True Range: `max(H-L, H-prevC, L-prevC)` |
| `sma_5` | 5-day simple moving average of close |
| `price_vs_sma` | `(current / sma_5 - 1) × 100` % |
| `dist_to_resistance_pct` | `(5d_high / current - 1) × 100` % |
| `dist_to_support_pct` | `(current / 5d_low - 1) × 100` % |
| `vol_ratio` | `today_volume / 20d_avg_volume` (excluding today) |
| `vwap` | From 1m intraday candles: `Σ(TP × vol) / Σvol` (only if include_vwap=True) |
| `price_vs_vwap` | `(current / vwap - 1) × 100` % |

**Note:** `include_vwap=False` used for watchlist bulk scan (111 tickers × 1m intraday call is too slow).

### Caching

| Data | Cache TTL |
|------|-----------|
| Current prices (get_current_prices) | 5 minutes |
| News search (DuckDuckGo) | 90 minutes |
| Technical data (get_stock_context) | No cache (fetched each call) |

---

## 11. Web Research

### search(query, max_results=5) — DuckDuckGo with cache

```
├── Check 90-min cache by query string
├── Call DuckDuckGo search
├── Filter out junk domains:
│     moneycontrol.com, nseindia.com, bseindia.com, investing.com,
│     tradingview.com, in.finance.yahoo.com, economictimes.indiatimes.com
├── Filter: snippet length ≥ 60 chars
├── Take up to max_results
├── Cache result
└── Fallback: yfinance.Ticker.news if DDG returns nothing
```

### build_research_context(tickers)

```
├── Broad market query: "India market Nifty Sensex today {date}"
├── Per-ticker queries: "{ticker} {company_name} stock news India today"
└── Format with headers per ticker, numbered results
```

---

## 12. Daily Review Flow

Called at end-of-day (Ctrl+C or `python run.py --review`):

```
TradingAgent.run_daily_review()
  │
  ├── portfolio.save_daily_snapshot(prices)
  │     └── Insert/replace into daily_snapshots table
  │
  ├── learner.generate_daily_review(llm_client)
  │     └── LLM reflects on today's trades → append to journal
  │
  ├── learner.update_distilled_rules(llm_client)
  │     └── LLM synthesises full journal → update Distilled Rules block
  │
  └── Log performance stats:
        {total_trades, wins, losses, win_rate, total_pnl, avg_win, avg_loss}
```

---

## 13. Dashboard API

FastAPI server (`uvicorn dashboard.app:app --port 8000`).

| Endpoint | Returns |
|----------|---------|
| `GET /` | `index.html` frontend |
| `GET /api/portfolio` | `{cash, holdings_value, total_value, total_return, holdings[]}` |
| `GET /api/trades/open` | Open positions with `direction`, `stop_price`, `target_price` |
| `GET /api/trades/closed?limit=30` | Recent closed trades with `direction`, `pnl` |
| `GET /api/risk` | Risk status, daily P&L, limits used |
| `GET /api/performance` | Win rate, avg win/loss, best/worst trade |
| `GET /api/learnings` | Journal contents (10k chars) |
| `GET /api/journal` | Full journal markdown |
| `GET /api/watchlist` | 111 stocks with current prices |
| `GET /api/snapshots?limit=30` | Daily snapshots for charting |
| `GET /api/logs?lines=150` | Recent agent log lines |
| `GET /api/config` | Agent configuration (non-sensitive) |

### Frontend Display

- **Holdings table**: LONG (green badge) / SHORT (red badge), stop & target prices visible
- **Trades table**: Direction badge on every closed trade
- **Portfolio value**: True net equity (short proceeds netted out correctly)

---

## 14. Configuration Reference

All settings in `agent/config.py:AgentConfig`.

### Capital & Risk Limits

| Setting | Default | Description |
|---------|---------|-------------|
| `starting_capital` | ₹10,00,000 | Paper trading starting balance |
| `max_position_pct` | 20% | Max single position as % of portfolio |
| `max_open_positions` | 5 | Max simultaneous open trades |
| `daily_loss_limit_pct` | 2% | Stop trading if day loss > 2% of capital |
| `per_trade_loss_limit_pct` | 1% | Legacy stop-loss for positions without ATR levels |
| `max_trade_amount` | ₹2,00,000 | Max size per individual trade |

### ATR-Based Stop/Target (computed at trade entry)

| Direction | Stop | Target | Risk:Reward |
|-----------|------|--------|-------------|
| LONG | entry − 1.5 × ATR | entry + 2.0 × ATR | 1:2 |
| SHORT | entry + 1.5 × ATR | entry − 2.0 × ATR | 1:2 |

### Trading Schedule

| Setting | Value |
|---------|-------|
| Market open | 09:15 IST |
| Market close | 15:30 IST |
| Intraday cycle | Every 15 minutes |
| Force-close intraday | 15:15 IST (no new trades either) |
| New positions blocked after | 15:15 IST |

### LLM Configuration

| Setting | Default |
|---------|---------|
| Provider | `openrouter` |
| Model | `anthropic/claude-haiku-4-5` |
| Max tokens per cycle | 4096 |
| Max tool iterations | 10 |
| Learnings context | 6000 chars (distilled rules + recent entries) |

---

## 15. Full Cycle Sequence Diagram

```
                        EVERY 15 MINUTES DURING MARKET HOURS
                        ─────────────────────────────────────

run.py:run_loop()
     │
     ├─ market closed? → sleep 60s → repeat
     │
     └─ run_once()
           │
     ┌─────▼──────────────────────────────────────────────────────────┐
     │  PHASE 1 — OBSERVE                                              │
     │                                                                 │
     │  yfinance ──────────► get_current_prices()    (5-min cache)    │
     │  yfinance ──────────► get_watchlist_summary() (111 stocks)     │
     │                         └─ RSI, ATR, SMA, vol_ratio, S/R dist │
     │  DuckDuckGo ────────► build_research_context() (90-min cache) │
     └────────────────────────────────┬────────────────────────────────┘
                                      │
     ┌────────────────────────────────▼────────────────────────────────┐
     │  PHASE 2 — RISK CHECK                                           │
     │                                                                 │
     │  get_risk_status() → daily P&L, can_trade                      │
     │  IF daily loss limit hit → RETURN (no trades today)            │
     └────────────────────────────────┬────────────────────────────────┘
                                      │
     ┌────────────────────────────────▼────────────────────────────────┐
     │  PHASE 3 — STOP/TARGET SWEEP                                    │
     │                                                                 │
     │  For each open position:                                        │
     │  ├─ LONG:  price ≤ stop? → execute_sell()                      │
     │  │         price ≥ target? → execute_sell()                    │
     │  └─ SHORT: price ≥ stop? → execute_cover()                     │
     │            price ≤ target? → execute_cover()                   │
     │                                                                 │
     │  Each closed trade → learner.write_trade_log() → journal entry  │
     └────────────────────────────────┬────────────────────────────────┘
                                      │
     ┌────────────────────────────────▼────────────────────────────────┐
     │  PHASE 4 — INTRADAY CLOSE (≥ 15:15 IST)                        │
     │                                                                 │
     │  IF time ≥ 15:15:                                               │
     │  ├─ Force-sell all open intraday LONG positions                 │
     │  ├─ Force-cover all open intraday SHORT positions               │
     │  └─ RETURN early (no LLM decision today)                       │
     └────────────────────────────────┬────────────────────────────────┘
                                      │ (only reached before 15:15)
     ┌────────────────────────────────▼────────────────────────────────┐
     │  PHASE 5 — DECISION ENGINE (LLM tool loop)                      │
     │                                                                 │
     │  Context sent to LLM:                                           │
     │  ├─ Portfolio state (cash, holdings, P&L)                      │
     │  ├─ Risk status                                                 │
     │  ├─ 111-stock watchlist (price, RSI, vol, S/R)                 │
     │  ├─ News context (market + top movers)                         │
     │  └─ Learnings (distilled rules + recent journal, 6000 chars)   │
     │                                                                 │
     │  LLM iterates (max 10 turns):                                   │
     │  ├─ search_market_news  → DuckDuckGo                           │
     │  ├─ get_portfolio_status → live portfolio snapshot              │
     │  ├─ get_stock_details   → full technicals + VWAP               │
     │  └─ place_trade:                                                │
     │        ├─ Hard guard: block if ≥ 15:15                         │
     │        ├─ Risk checks (6 hard limits)                           │
     │        ├─ Compute ATR stop/target                               │
     │        ├─ Execute in SQLite                                     │
     │        └─ Log to journal                                        │
     │                                                                 │
     │  LLM stops when no more tool calls                              │
     └────────────────────────────────┬────────────────────────────────┘
                                      │
     ┌────────────────────────────────▼────────────────────────────────┐
     │  RETURN + SLEEP 15 MIN                                          │
     │                                                                 │
     │  {status: "ok", actions: [...], portfolio: {...}}               │
     └─────────────────────────────────────────────────────────────────┘


                        END OF DAY (Ctrl+C or --review)
                        ────────────────────────────────

run_daily_review()
  ├─ save_daily_snapshot()         → SQLite daily_snapshots table
  ├─ generate_daily_review()       → LLM reflection → journal.md (append)
  └─ update_distilled_rules()      → LLM synthesis → journal.md (rules block updated)


                        WHAT THE LLM SEES NEXT CYCLE
                        ─────────────────────────────

get_learnings(max_chars=6000):
  ├─ ## 📌 Distilled Rules (always included, up to 3000 chars)
  │     "HAVELLS RSI <30 + VWAP = reliable bounce..."
  │     "Don't short defensive stocks on crash days..."
  │     ...
  │
  └─ [recent journal entries, up to remaining 3000 chars]
        "### 2026-04-02 14:35 — Entry: HAVELLS.NS"
        "### 2026-04-02 15:07 — Exit: HAVELLS.NS | WIN..."
        ...
```

---

*This document reflects the codebase state as of 2026-04-02.*
*Key files: `agent/runner.py`, `agent/decision_engine.py`, `agent/portfolio.py`, `agent/learner.py`*
