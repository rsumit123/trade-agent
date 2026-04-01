"""
Configuration for the AI Trading Agent.
All tunable parameters live here — risk limits, watchlist, API settings, etc.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class AgentConfig:
    # ── Capital & Portfolio ──────────────────────────────────
    starting_capital: float = 10_00_000.0  # ₹10 Lakhs
    currency: str = "INR"

    # ── Risk Limits (HARD — these override LLM decisions) ───
    max_position_pct: float = 0.20        # Max 20% of portfolio in one stock
    max_open_positions: int = 5           # Max concurrent positions
    daily_loss_limit_pct: float = 0.02    # Stop trading if down 2% in a day
    per_trade_loss_limit_pct: float = 0.01  # Stop-loss per trade: 1%
    max_trade_amount: float = 2_00_000.0  # Max ₹2L per trade

    # ── Watchlist (NSE symbols — 111 stocks across 18 sectors) ──
    watchlist: List[str] = field(default_factory=lambda: [
        # IT & Technology — Large Cap (7)
        "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS",
        "TECHM.NS", "PERSISTENT.NS", "COFORGE.NS",
        # IT — Mid Cap (4)
        "MPHASIS.NS", "LTTS.NS", "KPITTECH.NS", "TANLA.NS",
        # Banking — Large Cap (11)
        "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS",
        "INDUSINDBK.NS", "BANDHANBNK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
        "SBILIFE.NS", "HDFCLIFE.NS",
        # NBFCs (4)
        "MUTHOOTFIN.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "M&MFIN.NS",
        # Insurance (3)
        "ICICIGI.NS", "LICI.NS", "GICRE.NS",
        # Oil, Gas & Energy (8)
        "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS",
        "POWERGRID.NS", "NTPC.NS", "ADANIGREEN.NS", "TATAPOWER.NS",
        # Auto & Auto-ancillary (6)
        "MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
        "HEROMOTOCO.NS", "MOTHERSON.NS",
        # FMCG (7)
        "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS",
        "DABUR.NS", "MARICO.NS", "GODREJCP.NS",
        # Pharma & Healthcare (6)
        "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS",
        "DIVISLAB.NS", "APOLLOHOSP.NS", "AUROPHARMA.NS",
        # Infra, Capital Goods & Cement (6)
        "LT.NS", "ULTRACEMCO.NS", "GRASIM.NS",
        "ADANIPORTS.NS", "ABB.NS", "SIEMENS.NS",
        # Defence & Aerospace (4)
        "HAL.NS", "BEL.NS", "BEML.NS", "MAZDOCK.NS",
        # Metals & Mining (5)
        "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "COALINDIA.NS",
        # Chemicals (5)
        "SRF.NS", "DEEPAKNTR.NS", "NAVINFLUOR.NS", "ATUL.NS", "FINEORG.NS",
        # Telecom (2)
        "BHARTIARTL.NS", "IDEA.NS",
        # Consumer Durables & Electronics (4)
        "HAVELLS.NS", "VOLTAS.NS", "WHIRLPOOL.NS", "BLUESTARCO.NS",
        # Consumer & Retail (7)
        "TITAN.NS", "ASIANPAINT.NS", "PIDILITIND.NS", "TRENT.NS",
        "DMART.NS", "JUBLFOOD.NS", "WESTLIFE.NS",
        # QSR / Food (2)
        "SAPPHIRE.NS", "DEVYANI.NS",
        # Real Estate (5)
        "DLF.NS", "GODREJPROP.NS", "PRESTIGE.NS", "OBEROIRLTY.NS", "PHOENIXLTD.NS",
        # Hospitality & Travel (3)
        "INDHOTEL.NS", "LEMONTREE.NS", "THOMASCOOK.NS",
        # Media & Entertainment (3)
        "ZEEL.NS", "SUNTV.NS", "DISHTV.NS",
        # Logistics (4)
        "DELHIVERY.NS", "BLUEDART.NS", "CONCOR.NS", "ALLCARGO.NS",
        # Agriculture / Agrochemicals (2)
        "UPL.NS", "COROMANDEL.NS",
        # Chemicals & Materials (2)
        "TATACHEM.NS", "PCBL.NS",
        # Conglomerate (1)
        "ADANIENT.NS",
    ])

    # ── Trading Schedule ─────────────────────────────────────
    market_open: str = "09:15"       # IST
    market_close: str = "15:30"      # IST
    intraday_interval_min: int = 15  # Check every 15 minutes for intraday
    swing_check_times: List[str] = field(default_factory=lambda: [
        "09:30", "12:00", "15:00"    # Check 3x/day for swing trades
    ])
    timezone: str = "Asia/Kolkata"

    # ── LLM Configuration ────────────────────────────────────
    llm_provider: str = "openrouter"  # "anthropic", "openai", or "openrouter"
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    openai_model: str = "gpt-4o"
    openrouter_model: str = "anthropic/claude-haiku-4-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    @property
    def api_key(self) -> str:
        key_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env_var = key_map.get(self.llm_provider, "OPENROUTER_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise ValueError(f"Set {env_var} env var")
        return key

    # ── Paths ────────────────────────────────────────────────
    db_path: str = "data/trades.db"
    learnings_path: str = "learnings/journal.md"
    log_path: str = "logs/agent.log"

    # ── Web Research ─────────────────────────────────────────
    max_news_results: int = 5
    news_sources: List[str] = field(default_factory=lambda: [
        "moneycontrol", "economictimes", "livemint", "reuters", "bloomberg"
    ])
