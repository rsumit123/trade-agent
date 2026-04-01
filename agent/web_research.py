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

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Execute a real web search using DuckDuckGo.
        Returns list of {title, url, snippet, source} dicts.
        Falls back to empty list on failure (never crashes the agent).
        """
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results, region="in-en"):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "source": r.get("href", "").split("/")[2] if r.get("href") else "unknown",
                    })
            logger.info(f"DDG search '{query[:60]}' → {len(results)} results")
            return results
        except Exception as e:
            logger.warning(f"DDG search failed for '{query}': {e}")
            return []

    def get_news_for_stock(self, ticker: str) -> List[Dict[str, str]]:
        """
        Get recent news for a stock.
        First tries DuckDuckGo for fresh results, falls back to yfinance.
        """
        clean = ticker.replace(".NS", "").replace(".BO", "")
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

        # 1. Broad market overview via DDG
        market_news = self.search("NSE Nifty India stock market today", max_results=3)
        if market_news:
            sections.append(self.format_news_for_llm("India Market Overview", market_news))

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
