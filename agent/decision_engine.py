"""
Decision Engine — the LLM brain of the trading agent.
Sends market context + portfolio state to the LLM and gets structured trade decisions.
Supports Anthropic (Claude), OpenAI, and OpenRouter (all models via one key).
"""

import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from .config import AgentConfig

logger = logging.getLogger(__name__)

# ── System Prompt ────────────────────────────────────────────

TRADING_SYSTEM_PROMPT = """You are an autonomous AI trading agent managing a paper trading portfolio on Indian stock markets (NSE).

## Your Mandate
- Make profitable intraday and short-term swing trades
- Preserve capital — never risk more than you can afford to lose
- Learn from past trades and adapt your strategy

## Trading Rules
- INTRADAY trades must be closed before 3:15 PM IST
- SWING trades can be held for 1-5 days max
- Always have a clear thesis for every trade (why this stock, why now)
- Consider both technical signals AND news/sentiment
- When in doubt, do nothing — cash is a position too

## Available Tools
You have access to these tools:
1. `search_market_news` — Search web for market news and analysis
2. `get_portfolio_status` — Check your current portfolio, cash, and open positions
3. `get_stock_details` — Get detailed price/technical data for a stock
4. `place_trade` — Execute a BUY or SELL trade

## Decision Framework
1. First, assess overall market sentiment (bullish/bearish/neutral)
2. Check your portfolio status and risk capacity
3. Scan for opportunities in the watchlist
4. For each potential trade, research the stock's news and technicals
5. Only trade when you have conviction — explain your reasoning

## Risk Awareness
- Your trades are checked against hard risk limits (position size, daily loss, etc.)
- If a trade is rejected by the risk manager, accept it and move on
- Never try to circumvent risk limits

## Past Learnings
{learnings}

## Current Context
- Date/Time: {current_time}
- Market Status: {market_status}
"""

# ── Anthropic Implementation ────────────────────────────────

class AnthropicDecisionEngine:
    """Decision engine using Claude API with tool use."""

    def __init__(self, config: AgentConfig):
        self.config = config
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=config.api_key)
        except ImportError:
            raise ImportError("pip install anthropic")

    def run_decision_loop(
        self,
        portfolio_summary: Dict,
        watchlist_data: List[Dict],
        news_context: str,
        risk_status: Dict,
        learnings: str,
        tools: List[Dict],
        tool_handler: callable,
        is_market_open: bool = True,
        force_intraday: bool = False,
    ) -> List[Dict]:
        """
        Run the full decision loop with tool use.
        The LLM can call tools multiple times before deciding.
        Returns list of trade actions taken.
        """
        from .web_research import LLMWebSearchTool

        # Build the context message
        context = self._build_context(portfolio_summary, watchlist_data,
                                       news_context, risk_status)

        intraday_hint = (
            "\n\n⚠️  TESTING MODE: Prefer `trade_type: intraday` for any new trades this cycle."
            " Intraday trades must be closed before 3:15 PM IST."
            if force_intraday else ""
        )

        system = TRADING_SYSTEM_PROMPT.format(
            learnings=learnings or "No past learnings yet — this is the beginning.",
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M IST"),
            market_status="OPEN" if is_market_open else "CLOSED",
        ) + intraday_hint

        # Tool definitions
        tool_defs = [
            LLMWebSearchTool.tool_definition(),
            LLMWebSearchTool.get_portfolio_tool(),
            LLMWebSearchTool.get_stock_detail_tool(),
            LLMWebSearchTool.get_trade_tool(),
        ]

        messages = [{"role": "user", "content": context}]
        actions_taken = []
        max_iterations = 10  # Safety limit on tool use loops

        for i in range(max_iterations):
            try:
                response = self.client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=4096,
                    system=system,
                    tools=tool_defs,
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"LLM API error: {e}")
                break

            # Process response blocks
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Check if there are tool calls
            tool_uses = [b for b in assistant_content if b.type == "tool_use"]

            if not tool_uses:
                # LLM is done — extract any final text
                text_blocks = [b.text for b in assistant_content if b.type == "text"]
                if text_blocks:
                    logger.info(f"Agent reasoning: {' '.join(text_blocks)[:500]}")
                break

            # Handle tool calls
            tool_results = []
            for tool_use in tool_uses:
                result = tool_handler(tool_use.name, tool_use.input)

                # Track trade actions
                if tool_use.name == "place_trade":
                    actions_taken.append({
                        "tool": tool_use.name,
                        "input": tool_use.input,
                        "result": result,
                    })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(result) if isinstance(result, dict) else str(result),
                })

            messages.append({"role": "user", "content": tool_results})

        return actions_taken

    def _build_context(self, portfolio: Dict, watchlist: List[Dict],
                       news: str, risk: Dict) -> str:
        """Build the initial context message for the LLM."""
        # Show top 20 movers by absolute % change so LLM sees the best opportunities
        top_movers = watchlist[:20] if watchlist else []

        return f"""## Current Portfolio
```json
{json.dumps(portfolio, indent=2)}
```

## Risk Status
```json
{json.dumps(risk, indent=2)}
```

## Watchlist — Top Movers
```json
{json.dumps(top_movers, indent=2)}
```

## Recent Market News
{news}

---

Analyze the market conditions and your portfolio. Use your tools to research
specific stocks if needed, then decide whether to make any trades.
If no good opportunities exist, it's perfectly fine to hold and wait.

What trades, if any, should we make right now?"""


# ── OpenAI / OpenRouter Implementation ──────────────────────

class OpenRouterDecisionEngine:
    """
    Decision engine using OpenAI-compatible API.
    Works for OpenRouter (all models), OpenAI directly, or any compatible endpoint.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        try:
            import openai
        except ImportError:
            raise ImportError("pip install openai")

        base_url = config.openrouter_base_url if config.llm_provider == "openrouter" else None
        extra_headers = {"HTTP-Referer": "https://github.com/rsumit123/trade-agent"} if base_url else {}

        self.client = openai.OpenAI(
            api_key=config.api_key,
            base_url=base_url,
            default_headers=extra_headers,
        )
        self.model = config.openrouter_model if config.llm_provider == "openrouter" else config.openai_model

    def run_decision_loop(
        self,
        portfolio_summary: Dict,
        watchlist_data: List[Dict],
        news_context: str,
        risk_status: Dict,
        learnings: str,
        tools: List[Dict],
        tool_handler: callable,
        is_market_open: bool = True,
        force_intraday: bool = False,
    ) -> List[Dict]:
        from .web_research import LLMWebSearchTool

        context = self._build_context(portfolio_summary, watchlist_data, news_context, risk_status)
        intraday_hint = (
            "\n\n⚠️  TESTING MODE: Prefer `trade_type: intraday` for any new trades this cycle."
            " Intraday trades must be closed before 3:15 PM IST."
            if force_intraday else ""
        )
        system = TRADING_SYSTEM_PROMPT.format(
            learnings=learnings or "No past learnings yet — this is the beginning.",
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M IST"),
            market_status="OPEN" if is_market_open else "CLOSED",
        ) + intraday_hint

        # Convert Anthropic-style tool schemas to OpenAI function format
        tool_defs = [
            self._to_openai_tool(LLMWebSearchTool.tool_definition()),
            self._to_openai_tool(LLMWebSearchTool.get_portfolio_tool()),
            self._to_openai_tool(LLMWebSearchTool.get_stock_detail_tool()),
            self._to_openai_tool(LLMWebSearchTool.get_trade_tool()),
        ]

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ]
        actions_taken = []
        max_iterations = 10

        for _ in range(max_iterations):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=4096,
                    tools=tool_defs,
                    tool_choice="auto",
                    messages=messages,
                )
            except Exception as e:
                logger.error(f"LLM API error: {e}")
                break

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                if msg.content:
                    logger.info(f"Agent reasoning: {msg.content[:500]}")
                break

            # Handle tool calls
            for tc in msg.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                result = tool_handler(tc.function.name, tool_input)

                if tc.function.name == "place_trade":
                    actions_taken.append({
                        "tool": tc.function.name,
                        "input": tool_input,
                        "result": result,
                    })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result) if isinstance(result, dict) else str(result),
                })

        return actions_taken

    def _build_context(self, portfolio: Dict, watchlist: List[Dict],
                       news: str, risk: Dict) -> str:
        top_movers = watchlist[:20] if watchlist else []
        return f"""## Current Portfolio
```json
{json.dumps(portfolio, indent=2)}
```

## Risk Status
```json
{json.dumps(risk, indent=2)}
```

## Watchlist — Top Movers
```json
{json.dumps(top_movers, indent=2)}
```

## Recent Market News
{news}

---

Analyze the market conditions and your portfolio. Use your tools to research
specific stocks if needed, then decide whether to make any trades.
If no good opportunities exist, it's perfectly fine to hold and wait.

What trades, if any, should we make right now?"""

    @staticmethod
    def _to_openai_tool(anthropic_tool: Dict) -> Dict:
        """Convert Anthropic tool schema → OpenAI function tool format."""
        return {
            "type": "function",
            "function": {
                "name": anthropic_tool["name"],
                "description": anthropic_tool["description"],
                "parameters": anthropic_tool["input_schema"],
            }
        }


def create_engine(config: AgentConfig):
    """Factory function to create the right engine."""
    if config.llm_provider == "anthropic":
        return AnthropicDecisionEngine(config)
    elif config.llm_provider in ("openrouter", "openai"):
        return OpenRouterDecisionEngine(config)
    else:
        raise ValueError(f"Unknown LLM provider: {config.llm_provider}")
