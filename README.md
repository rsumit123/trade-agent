# 🤖 AI Stock Trading Agent (Paper Trading)

An autonomous AI-powered trading agent for Indian markets (NSE/BSE) that uses LLM reasoning
to make intraday and short-term swing trades with virtual money.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  AGENT LOOP                      │
│  (runs every N minutes during market hours)      │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ OBSERVE  │→ │ DECIDE   │→ │  ACT     │      │
│  │          │  │          │  │          │      │
│  │• Prices  │  │• LLM w/  │  │• Paper   │      │
│  │• News    │  │  tools   │  │  trades  │      │
│  │• Portfolio│  │• Risk    │  │• Logging │      │
│  │• Learnings│ │  checks  │  │          │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                    │             │
│                              ┌─────▼─────┐      │
│                              │  LEARN    │      │
│                              │           │      │
│                              │• Review   │      │
│                              │• Reflect  │      │
│                              │• Write MD │      │
│                              └───────────┘      │
└─────────────────────────────────────────────────┘
```

## Project Structure

```
ai-trader/
├── agent/
│   ├── __init__.py
│   ├── config.py          # All configuration & risk parameters
│   ├── portfolio.py       # Paper trading engine & portfolio state
│   ├── market_data.py     # Stock price fetching (yfinance / NSE)
│   ├── web_research.py    # News & sentiment via web search
│   ├── decision_engine.py # LLM-based trade decision maker
│   ├── risk_manager.py    # Hard risk limits (non-negotiable)
│   ├── learner.py         # Trade review & learning journal
│   └── runner.py          # Main agent loop orchestrator
├── data/
│   └── trades.db          # SQLite DB (auto-created)
├── learnings/
│   └── journal.md         # Agent's self-reflection journal
├── logs/
│   └── agent.log          # Execution logs
├── dashboard/
│   └── app.py             # FastAPI dashboard (optional)
├── requirements.txt
├── run.py                 # Entry point
└── README.md
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your LLM API key
export ANTHROPIC_API_KEY="your-key-here"
# OR
export OPENAI_API_KEY="your-key-here"

# 3. Run the agent
python run.py

# 4. (Optional) Run the dashboard
uvicorn dashboard.app:app --reload
```

## Configuration

Edit `agent/config.py` to set:
- Starting capital (default: ₹10,00,000)
- Max position size (default: 20% of portfolio)
- Daily loss limit (default: 2%)
- Watchlist (default: NIFTY 50 stocks)
- Agent check interval (default: 15 min for intraday)
- LLM provider (Claude or OpenAI)

## How It Works

1. **Observe**: Fetches current prices, portfolio state, recent news
2. **Decide**: Sends context to LLM, gets structured trade recommendations
3. **Risk Check**: Hard-coded limits override LLM suggestions
4. **Act**: Executes trades on paper trading engine
5. **Learn**: End-of-day review writes reflections to `learnings/journal.md`

## SEBI Disclaimer

This is a paper trading / educational project. If you connect to a real broker API,
ensure compliance with SEBI's algo trading regulations for retail investors.
