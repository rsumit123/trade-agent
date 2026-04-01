"""
Web Research Module — fetches market news and sentiment for the agent.
Uses the LLM's web search capability or falls back to RSS/scraping.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class WebResearcher:
    """
    Gathers market news and sentiment.

    Strategy: Rather than scraping directly, we let the LLM do web research
    as a tool call. This module prepares the research queries and parses results.

    For standalone use (without LLM tool-use), it falls back to yfinance news.
    """

    def __init__(self, news_sources: List[str] = None):
        self.sources = news_sources or [
            "moneycontrol", "economictimes", "livemint"
        ]

    def get_news_for_stock(self, ticker: str) -> List[Dict[str, str]]:
        """Get recent news for a specific stock using yfinance."""
        import yfinance as yf
        try:
            stock = yf.Ticker(ticker)
            news = stock.news or []
            results = []
            for item in news[:5]:
                content = item.get("content", {})
                results.append({
                    "title": content.get("title", "No title"),
                    "summary": content.get("summary", ""),
                    "source": content.get("provider", {}).get("displayName", "Unknown"),
                    "published": content.get("pubDate", ""),
                    "url": content.get("canonicalUrl", {}).get("url", ""),
                })
            return results
        except Exception as e:
            logger.warning(f"Failed to fetch news for {ticker}: {e}")
            return []

    def get_market_overview_queries(self) -> List[str]:
        """
        Generate search queries for general market sentiment.
        These get passed to the LLM's web_search tool.
        """
        return [
            "Indian stock market today NSE NIFTY",
            "FII DII activity today India",
            "India market sentiment today bullish bearish",
        ]

    def get_stock_research_queries(self, ticker: str) -> List[str]:
        """Generate search queries for a specific stock."""
        clean_name = ticker.replace(".NS", "").replace(".BO", "")
        return [
            f"{clean_name} stock news today",
            f"{clean_name} quarterly results outlook",
        ]

    def format_news_for_llm(self, ticker: str, news: List[Dict]) -> str:
        """Format news into a concise string for LLM context."""
        if not news:
            return f"No recent news found for {ticker}."

        lines = [f"Recent news for {ticker}:"]
        for i, item in enumerate(news[:3], 1):
            lines.append(f"  {i}. [{item['source']}] {item['title']}")
            if item.get("summary"):
                # Truncate long summaries
                summary = item["summary"][:150]
                lines.append(f"     {summary}...")
        return "\n".join(lines)

    def build_research_context(self, tickers: List[str]) -> str:
        """Build a consolidated research brief for the LLM."""
        sections = []

        for ticker in tickers[:5]:  # Limit to avoid too much context
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
    def tool_definition() -> Dict[str, Any]:
        """Returns the tool schema for the LLM."""
        return {
            "name": "search_market_news",
            "description": (
                "Search the web for recent market news, stock-specific news, "
                "sector analysis, or economic indicators relevant to Indian markets. "
                "Use this to gather information before making trade decisions."
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
    def get_stock_detail_tool() -> Dict[str, Any]:
        """Tool for the LLM to get detailed stock data."""
        return {
            "name": "get_stock_details",
            "description": (
                "Get detailed price data, technical indicators, and recent performance "
                "for a specific stock. Use before making a trade decision."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "NSE ticker symbol (e.g., 'RELIANCE.NS')"
                    }
                },
                "required": ["ticker"]
            }
        }

    @staticmethod
    def get_trade_tool() -> Dict[str, Any]:
        """Tool for the LLM to place trades."""
        return {
            "name": "place_trade",
            "description": (
                "Place a paper trade (buy or sell). For BUY: opens a new position. "
                "For SELL: closes an existing position by trade_id. "
                "All trades are subject to risk limits."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL"],
                        "description": "Trade action"
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker (required for BUY)"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of shares (required for BUY)"
                    },
                    "trade_type": {
                        "type": "string",
                        "enum": ["intraday", "swing"],
                        "description": "Trade holding period type"
                    },
                    "trade_id": {
                        "type": "integer",
                        "description": "Trade ID to close (required for SELL)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reasoning for this trade"
                    }
                },
                "required": ["action", "reason"]
            }
        }
