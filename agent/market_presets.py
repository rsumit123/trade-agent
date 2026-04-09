"""
Market Presets — canonical definitions for each supported market.

Every market-specific behaviour (timezone, hours, currency, watchlist,
news queries, etc.) flows from these presets.  Other modules import
`get_preset()` instead of hardcoding market assumptions.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MarketPreset:
    market_id: str                          # "nse", "crypto"
    display_name: str                       # "Indian Stock Markets (NSE)"
    currency: str                           # "INR", "USD"
    currency_symbol: str                    # "₹", "$"
    timezone: str                           # "Asia/Kolkata", "UTC"
    market_open: Optional[str]              # "09:15" or None (24/7)
    market_close: Optional[str]             # "15:30" or None (24/7)
    intraday_close_buffer_min: int          # 15 min before close
    is_24x7: bool                           # True for crypto
    default_watchlist: List[str] = field(default_factory=list)
    news_region: str = "us-en"              # DuckDuckGo region
    news_queries: List[str] = field(default_factory=list)
    news_sources: List[str] = field(default_factory=list)
    ticker_suffix: str = ""                 # ".NS" for NSE, "-USD" for crypto
    default_starting_capital: float = 10_000.0
    trade_types: List[str] = field(default_factory=lambda: ["swing"])
    short_allowed: bool = True
    locale: str = "en-US"                   # for number formatting
    default_watchlist_count: int = 0         # for UI display (0 = use len(watchlist))
    use_premarket_scanner: bool = False      # run pre-market scan before first cycle


# ── NSE (Indian Stock Markets) ─────────────────────────────────────────

NSE_PRESET = MarketPreset(
    market_id="nse",
    display_name="Indian Stock Markets (NSE)",
    currency="INR",
    currency_symbol="₹",
    timezone="Asia/Kolkata",
    market_open="09:15",
    market_close="15:30",
    intraday_close_buffer_min=15,
    is_24x7=False,
    locale="en-IN",
    default_watchlist=[
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
    ],
    news_region="in-en",
    news_queries=[
        "Indian stock market today NSE NIFTY",
        "FII DII activity today India",
        "India market sentiment today bullish bearish",
    ],
    news_sources=["moneycontrol", "economictimes", "livemint", "reuters", "bloomberg"],
    ticker_suffix=".NS",
    default_starting_capital=10_00_000.0,
    trade_types=["intraday", "swing"],
    short_allowed=True,
)


# ── Cryptocurrency ─────────────────────────────────────────────────────

CRYPTO_PRESET = MarketPreset(
    market_id="crypto",
    display_name="Cryptocurrency Markets",
    currency="USD",
    currency_symbol="$",
    timezone="UTC",
    market_open=None,
    market_close=None,
    intraday_close_buffer_min=0,
    is_24x7=True,
    locale="en-US",
    default_watchlist=[
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD",
        "AVAX-USD", "DOT-USD", "LINK-USD", "DOGE-USD", "MATIC-USD",
        "NEAR-USD", "UNI-USD", "AAVE-USD", "LTC-USD", "ATOM-USD",
    ],
    news_region="us-en",
    news_queries=[
        "crypto market today bitcoin ethereum",
        "crypto market sentiment today bullish bearish",
    ],
    news_sources=["coindesk", "cointelegraph", "decrypt", "theblock"],
    ticker_suffix="-USD",
    default_starting_capital=10_000.0,
    trade_types=["swing"],       # No intraday concept for 24/7 markets
    short_allowed=False,          # Simplified: no paper shorts on crypto
)


# ── NSE Intraday Scalping ──────────────────────────────────────────────

NSE_INTRADAY_PRESET = MarketPreset(
    market_id="nse-intraday",
    display_name="NSE Intraday Scalping",
    currency="INR",
    currency_symbol="₹",
    timezone="Asia/Kolkata",
    market_open="09:15",
    market_close="15:30",
    intraday_close_buffer_min=15,
    is_24x7=False,
    locale="en-IN",
    # Watchlist is DYNAMIC — built by pre-market scanner each morning
    # This default is a fallback if scanner doesn't run
    default_watchlist=[
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "SBIN.NS", "BHARTIARTL.NS", "HINDUNILVR.NS", "LT.NS", "KOTAKBANK.NS",
        "AXISBANK.NS", "MARUTI.NS", "TATASTEEL.NS", "SUNPHARMA.NS", "TITAN.NS",
        "WIPRO.NS", "BAJFINANCE.NS", "TATAMOTORS.NS", "ADANIENT.NS", "NTPC.NS",
    ],
    news_region="in-en",
    news_queries=[
        "Indian stock market today {date} news",
        "NSE Nifty intraday movers today",
    ],
    news_sources=["moneycontrol", "economictimes", "livemint", "business-standard"],
    ticker_suffix=".NS",
    default_starting_capital=5_00_000.0,   # ₹5L for scalping
    trade_types=["intraday"],               # ONLY intraday — no swing trades
    short_allowed=True,
    default_watchlist_count=25,
    use_premarket_scanner=True,             # Build dynamic watchlist each morning
)


# ── Registry ───────────────────────────────────────────────────────────

MARKET_PRESETS = {
    "nse": NSE_PRESET,
    "nse-intraday": NSE_INTRADAY_PRESET,
    "crypto": CRYPTO_PRESET,
}


def get_preset(market_id: str) -> MarketPreset:
    """Get a market preset by ID.  Raises ValueError for unknown markets."""
    if market_id not in MARKET_PRESETS:
        raise ValueError(
            f"Unknown market: '{market_id}'. "
            f"Available: {list(MARKET_PRESETS.keys())}"
        )
    return MARKET_PRESETS[market_id]
