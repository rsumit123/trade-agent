"""
Web Research Module — fetches market news and sentiment for the agent.
Uses the LLM's web search capability or falls back to RSS/scraping.
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class WebResearcher:
    """
    Gathers market news and sentiment.

    Strategy: Rather than scraping directly, we let the LLM do web research
    as a tool call. This module prepares the research queries and parses results.

    For standalone use (without LLM tool-use), it falls back to yfinance news.
    """

    # Shared in-memory news cache: {query: (fetched_at, results)}
    # 90-minute TTL — news doesn't change every 15 minutes
    _news_cache: Dict[str, Tuple[datetime, List]] = {}
    _CACHE_TTL = timedelta(minutes=90)

    def __init__(self, news_sources: List[str] = None, market_preset=None):
        self.market_preset = market_preset
        self.sources = news_sources or (
            market_preset.news_sources if market_preset else
            ["moneycontrol", "economictimes", "livemint"]
        )
        # Set True during backtest to disable all live web searches
        # (would leak future info into past decisions and pollute logs)
        self.backtest_mode = False

    # Homepages/aggregators that return boilerplate instead of real articles
    _JUNK_DOMAINS = {
        "moneycontrol.com", "tradingview.com", "nseindia.com",
        "bseindia.com", "google.com", "finance.yahoo.com",
        "investing.com", "marketwatch.com",
    }

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Execute a real web search using DuckDuckGo.
        Filters out homepage/aggregator junk so LLM gets actual article content.
        Falls back to empty list on failure (never crashes the agent).
        Results are cached for 90 minutes so repeated identical queries don't
        hit the network every 15-minute cycle.
        """
        # In backtest mode: NEVER hit the live web — would leak future
        # news (today's articles) into past decisions
        if self.backtest_mode:
            logger.debug(f"🔒 search '{query[:50]}' skipped (backtest mode)")
            return []

        # ── Cache check ──────────────────────────────────────────────
        now = datetime.now()
        if query in self._news_cache:
            cached_at, cached_results = self._news_cache[query]
            age_min = int((now - cached_at).total_seconds() / 60)
            if now - cached_at < self._CACHE_TTL:
                logger.debug(f"📦 News cache hit '{query[:50]}' (age {age_min}m)")
                return cached_results

        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            raw = []
            # Fetch extra results so we have enough after filtering
            with DDGS() as ddgs:
                region = self.market_preset.news_region if self.market_preset else "in-en"
                for r in ddgs.text(query, max_results=max_results + 6, region=region):
                    raw.append(r)

            results = []
            for r in raw:
                url = r.get("href", "")
                domain = url.split("/")[2] if url else ""
                snippet = r.get("body", "")
                # Skip junk domains and results with no real snippet content
                if any(j in domain for j in self._JUNK_DOMAINS):
                    continue
                if len(snippet) < 60:
                    continue
                results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": snippet,
                    "source": domain,
                })
                if len(results) >= max_results:
                    break

            logger.info(f"DDG search '{query[:60]}' → {len(results)} results (filtered from {len(raw)})")
            # Store in cache before returning
            self._news_cache[query] = (now, results)
            return results
        except Exception as e:
            logger.warning(f"DDG search failed for '{query}': {e}")
            return []

    def get_news_for_stock(self, ticker: str) -> List[Dict[str, str]]:
        """
        Get recent news for a stock.
        First tries DuckDuckGo for fresh results, falls back to yfinance.
        """
        if self.backtest_mode:
            return []
        clean = ticker.replace(".NS", "").replace(".BO", "").replace("-USD", "")
        if self.market_preset and self.market_preset.market_id == "crypto":
            query = f"{clean} cryptocurrency news today"
        else:
            query = f"{clean} stock news India today"
        results = self.search(query, max_results=4)
        if results:
            return results

        # Fallback: yfinance news
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            news = stock.news or []
            results = []
            for item in news[:5]:
                content = item.get("content", {})
                results.append({
                    "title": content.get("title", "No title"),
                    "snippet": content.get("summary", ""),
                    "source": content.get("provider", {}).get("displayName", "Unknown"),
                    "url": content.get("canonicalUrl", {}).get("url", ""),
                })
            return results
        except Exception as e:
            logger.warning(f"yfinance news fallback failed for {ticker}: {e}")
            return []

    def get_market_overview_queries(self) -> List[str]:
        """
        Generate search queries for general market sentiment.
        These get passed to the LLM's web_search tool.
        """
        if self.market_preset and self.market_preset.news_queries:
            return list(self.market_preset.news_queries)
        return [
            "Indian stock market today NSE NIFTY",
            "FII DII activity today India",
            "India market sentiment today bullish bearish",
        ]

    def get_stock_research_queries(self, ticker: str) -> List[str]:
        """Generate search queries for a specific asset."""
        clean_name = ticker.replace(".NS", "").replace(".BO", "").replace("-USD", "")
        if self.market_preset and self.market_preset.market_id == "crypto":
            return [
                f"{clean_name} crypto news today",
                f"{clean_name} price analysis outlook",
            ]
        return [
            f"{clean_name} stock news today",
            f"{clean_name} quarterly results outlook",
        ]

    def format_news_for_llm(self, label: str, news: List[Dict]) -> str:
        """Format news results into a concise string for LLM context."""
        if not news:
            return f"No recent news found for {label}."

        lines = [f"News — {label}:"]
        for i, item in enumerate(news[:4], 1):
            title = item.get("title", "No title")
            source = item.get("source", "")
            snippet = item.get("snippet", item.get("summary", ""))[:180]
            lines.append(f"  {i}. [{source}] {title}")
            if snippet:
                lines.append(f"     {snippet}")
        return "\n".join(lines)

    def build_research_context(self, tickers: List[str]) -> str:
        """
        Build a consolidated research brief for the LLM.
        Includes a broad market overview + top-5 stock-specific news.
        """
        sections = []

        # 1. Broad market overview via DDG — date-specific for fresh articles
        from datetime import date
        today = date.today().strftime("%B %d %Y")
        overview_queries = self.get_market_overview_queries()
        for q in overview_queries[:2]:
            market_news = self.search(f"{q} {today} news", max_results=3)
            if market_news:
                label = self.market_preset.display_name if self.market_preset else "Market"
                sections.append(self.format_news_for_llm(f"{label} Overview", market_news))
                break  # One overview section is enough

        # 2. Stock-specific news for top 5 tickers
        for ticker in tickers[:5]:
            news = self.get_news_for_stock(ticker)
            if news:
                sections.append(self.format_news_for_llm(ticker, news))

        if not sections:
            return "No market news available at this time."

        return "\n\n".join(sections)


class LLMWebSearchTool:
    """
    Tool definition for LLM-based web search.
    When using Claude/OpenAI with tool-use, this defines the web search tool
    that the agent can invoke during its reasoning.
    """

    @staticmethod
    def tool_definition(market_preset=None) -> Dict[str, Any]:
        """Returns the tool schema for the LLM."""
        market_desc = market_preset.display_name if market_preset else "financial markets"
        return {
            "name": "search_market_news",
            "description": (
                f"Search the web for recent market news, asset-specific news, "
                f"sector analysis, or economic indicators relevant to {market_desc}. "
                f"Use this to gather information before making trade decisions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for market research"
                    }
                },
                "required": ["query"]
            }
        }

    @staticmethod
    def get_portfolio_tool() -> Dict[str, Any]:
        """Tool for the LLM to check current portfolio state."""
        return {
            "name": "get_portfolio_status",
            "description": (
                "Get current portfolio status including cash balance, open positions, "
                "unrealized P&L, and today's trading activity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }

    @staticmethod
    def get_stock_detail_tool(market_preset=None) -> Dict[str, Any]:
        """Tool for the LLM to get detailed asset data."""
        if market_preset and market_preset.market_id == "crypto":
            example = "'BTC-USD'"
            label = "Cryptocurrency ticker"
        else:
            example = "'RELIANCE.NS'"
            label = "Ticker symbol"
        return {
            "name": "get_stock_details",
            "description": (
                "Get detailed price data, technical indicators, and recent performance "
                "for a specific asset. Use before making a trade decision."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": f"{label} (e.g., {example})"
                    }
                },
                "required": ["ticker"]
            }
        }

    @staticmethod
    def get_trade_tool(market_preset=None) -> Dict[str, Any]:
        """Tool for the LLM to place trades."""
        if market_preset and market_preset.is_24x7:
            short_note = ""
            force_note = ""
        else:
            close_time = market_preset.market_close if market_preset else "15:30"
            buffer = market_preset.intraday_close_buffer_min if market_preset else 15
            close_h, close_m = map(int, close_time.split(":"))
            trigger_m = close_m - buffer
            trigger_h = close_h
            if trigger_m < 0:
                trigger_m += 60
                trigger_h -= 1
            tz_label = market_preset.timezone if market_preset else "IST"
            short_note = f" — INTRADAY ONLY"
            force_note = (
                f" SHORT positions are force-covered at {trigger_h}:{trigger_m:02d} "
                f"({tz_label}) if not closed manually."
            )
        return {
            "name": "place_trade",
            "description": (
                "Place a paper trade. Actions:\n"
                "  BUY: open a long position (ticker + quantity required).\n"
                "  SELL: close a long position (trade_id required).\n"
                f"  SHORT: open a short/bearish position{short_note} (ticker + quantity required). "
                "Use when you expect the price to fall.\n"
                "  COVER: close a short position by buying back (trade_id required).\n"
                f"All trades are subject to risk limits.{force_note}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL", "SHORT", "COVER"],
                        "description": "Trade action: BUY/SELL for long trades, SHORT/COVER for short trades"
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker, e.g. 'RELIANCE.NS' (required for BUY and SHORT)"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of shares (required for BUY and SHORT)"
                    },
                    "trade_type": {
                        "type": "string",
                        "enum": ["intraday", "swing"],
                        "description": "Holding period — SHORT is always intraday regardless of this value"
                    },
                    "trade_id": {
                        "type": "integer",
                        "description": "Trade ID to close (required for SELL and COVER)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Detailed reasoning: include ticker, RSI, volume ratio, support/resistance levels, news catalyst if any, and which Distilled Rule supports this trade"
                    },
                    "conviction": {
                        "type": "integer",
                        "description": "Your conviction level 1-5. 1=speculative guess, 2=weak signal, 3=moderate, 4=strong setup, 5=textbook setup with multiple confirmations. Only trade at 4-5 conviction."
                    }
                },
                "required": ["action", "reason", "conviction"]
            }
        }

    @staticmethod
    def get_update_levels_tool() -> Dict[str, Any]:
        """Tool for adjusting stop/target on an existing open position
        (e.g. trailing stop after a favorable move, tightening stop on
        signs of reversal). Cannot change ticker or quantity — those
        require closing and re-opening."""
        return {
            "name": "update_levels",
            "description": (
                "Adjust the stop_price or target_price of an OPEN trade. "
                "Use this to trail a stop after a favorable move, tighten "
                "a stop when reversal signs appear, or extend a target. "
                "Provide at least one of stop_price or target_price. "
                "trade_id is required and must point to an open trade."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "trade_id": {
                        "type": "integer",
                        "description": "ID of the OPEN trade whose levels you want to adjust"
                    },
                    "stop_price": {
                        "type": "number",
                        "description": "New stop_price (omit to leave unchanged)"
                    },
                    "target_price": {
                        "type": "number",
                        "description": "New target_price (omit to leave unchanged)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why are you adjusting? Reference the rule or signal."
                    },
                },
                "required": ["trade_id", "reason"]
            }
        }
