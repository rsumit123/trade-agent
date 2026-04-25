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


# ── System Prompt Template ──────────────────────────────────

TRADING_SYSTEM_PROMPT = """You are an autonomous AI trading agent managing a paper trading portfolio on {market_name}.

## Your Mandate
- Make profitable trades by following your learned rules
- Preserve capital — never risk more than you can afford to lose
- Actively learn from past trades and adapt your strategy

## Trading Rules
{trading_rules}

## Long vs Short
- Use BUY (long) when you expect the price to RISE
- Use SHORT when you expect the price to FALL (bearish setup: RSI >70 overbought, near resistance, negative news)
- Use SELL to close a BUY position; use COVER to close a SHORT position
- For SHORT: stop is ABOVE entry (price rising against you), target is BELOW entry (price falling in your favour)

## Available Tools
You have access to these tools:
1. `search_market_news` — Search web for market news and analysis
2. `get_portfolio_status` — Check your current portfolio, cash, and open positions
3. `get_stock_details` — Get detailed price/technical data for an asset
4. `place_trade` — Execute BUY / SELL (long) or SHORT / COVER (short) trades

## Decision Framework
1. First, assess overall market sentiment (bullish/bearish/neutral)
2. Check your portfolio status and risk capacity
3. **READ your Distilled Rules below** — these are lessons from your own past trades
4. Scan for opportunities in the watchlist — both long AND short setups
5. For each candidate trade, **check if a Distilled Rule applies**:
   - If a rule SUPPORTS this setup → proceed with higher confidence
   - If a rule WARNS AGAINST this setup → do NOT take the trade unless you have a strong reason to override (explain why)
6. For each potential trade, research the asset's news and technicals
7. Only trade when you have conviction (4-5 out of 5) — explain your reasoning

## Risk Awareness
- Your trades are checked against hard risk limits (position size, daily loss, etc.)
- If a trade is rejected by the risk manager, accept it and move on
- Never try to circumvent risk limits

{personality_section}

## Past Learnings (YOUR OWN TRADE HISTORY — READ CAREFULLY)
{learnings}

**IMPORTANT**: The Distilled Rules above are extracted from YOUR OWN past trades. They represent patterns that have been proven by your actual results. Follow them unless you have a compelling, specific reason not to.

## Current Context
- Date/Time: {current_time}
- Market Status: {market_status}
"""


def _build_trading_rules(preset) -> str:
    """Build market-specific trading rules for the system prompt."""
    if preset and preset.is_24x7:
        return (
            "- This is a 24/7 market — no forced close times\n"
            "- SWING trades can be held for days\n"
            "- Always have a clear thesis for every trade (why this asset, why now)\n"
            "- Consider both technical signals AND news/sentiment\n"
            "- When in doubt, do nothing — cash is a position too"
        )
    elif preset and preset.market_id == "nse-intraday":
        close_time = preset.market_close or "15:30"
        buffer = preset.intraday_close_buffer_min or 15
        close_h, close_m = map(int, close_time.split(":"))
        trigger_m = close_m - buffer
        trigger_h = close_h
        if trigger_m < 0:
            trigger_m += 60
            trigger_h -= 1
        trigger_str = f"{trigger_h}:{trigger_m:02d}"
        return (
            "## INTRADAY SCALPING RULES\n"
            f"- ALL trades are INTRADAY ONLY — auto-closed at {trigger_str} IST\n"
            "- NO swing trades allowed — you must exit everything before market close\n"
            "- Target: +0.3% to +1.0% per trade (tight scalps)\n"
            "- Stop loss: -0.3% to -0.5% max per trade (cut fast)\n"
            "- Hold time: 5-45 minutes typical, rarely more than 1 hour\n"
            "- Position sizing: use full allocation, multiple simultaneous positions OK\n"
            "- LONG setups: gap-up + volume spike + RSI not overbought + VWAP support\n"
            "- SHORT setups: gap-down + high volume + RSI overbought + below VWAP\n"
            "- First 15 min (9:15-9:30): OBSERVE ONLY — let volatility settle\n"
            "- Prime scalping window: 9:30 AM - 2:30 PM IST\n"
            "- After 2:30 PM: NO new entries, only exits\n"
            "- Exit immediately if thesis breaks (price below VWAP for longs, above for shorts)\n"
            "- Pre-market watchlist was built by scanning ALL NSE stocks — trade these high-quality picks\n"
            "- Focus on stocks with volume 1.5x+ average — liquidity enables clean entries/exits\n"
        )
    else:
        close_time = preset.market_close if preset else "15:30"
        buffer = preset.intraday_close_buffer_min if preset else 15
        tz = preset.timezone if preset else "IST"
        close_h, close_m = map(int, close_time.split(":"))
        trigger_m = close_m - buffer
        trigger_h = close_h
        if trigger_m < 0:
            trigger_m += 60
            trigger_h -= 1
        trigger_str = f"{trigger_h}:{trigger_m:02d}"
        return (
            f"- INTRADAY trades must be closed before {trigger_str} ({tz}) (auto-closed if you don't)\n"
            "- SWING trades can be held for 1-5 days max\n"
            f"- SHORT positions are ALWAYS intraday — force-covered at {trigger_str} ({tz})\n"
            "- Always have a clear thesis for every trade (why this asset, why now)\n"
            "- Consider both technical signals AND news/sentiment\n"
            "- When in doubt, do nothing — cash is a position too"
        )


def _format_system_prompt(config, learnings, is_market_open, force_intraday=False, is_backtest=False, backtest_date=None, backtest_time=None):
    """Format the system prompt with dynamic values."""
    preset = config._market_preset
    market_name = preset.display_name if preset else "Indian Stock Markets (NSE)"
    tz = preset.timezone if preset else "Asia/Kolkata"

    trading_rules = _build_trading_rules(preset)

    # Personality section from session config
    personality_section = ""
    sc = config._session_config
    if sc and sc.personality:
        personality_section = f"## Trading Personality\n{sc.personality}"

    # Current time — use simulated time during backtest, real time otherwise
    if is_backtest and backtest_date:
        time_str = f"{backtest_date} {backtest_time or ''} (SIMULATED, {tz})".strip()
    else:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(tz))
            time_str = now.strftime(f"%Y-%m-%d %H:%M ({tz})")
        except Exception:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    system = TRADING_SYSTEM_PROMPT.format(
        market_name=market_name,
        trading_rules=trading_rules,
        personality_section=personality_section,
        learnings=learnings or "No past learnings yet — this is the beginning.",
        current_time=time_str,
        market_status="OPEN" if is_market_open else "CLOSED",
    )

    # Backtest mode addendum: tell the LLM to skip news + rely on price action
    if is_backtest:
        system += (
            "\n\n## BACKTEST MODE ACTIVE\n"
            "You are replaying a historical trading day. Live news searches are DISABLED — "
            "the `search_market_news` tool will return empty results.\n"
            "**Trade purely on price action and quantitative signals:**\n"
            "- Technical indicators: RSI, ATR, EMA20/50, MACD, VWAP, volume ratio\n"
            "- Support/resistance levels (from 5-day high/low)\n"
            "- Pre-market scanner thesis (which stocks were picked and why)\n"
            "- Your distilled rules from past backtest days\n"
            "**Do NOT call `search_market_news`** — it returns nothing in backtest mode and wastes a tool round-trip.\n"
            "Simulated date/time is shown in Current Context — treat it as 'now'."
        )

    # Intraday hint (only for scheduled markets)
    if force_intraday and preset and not preset.is_24x7:
        close_str = preset.market_close or "15:30"
        buffer = preset.intraday_close_buffer_min or 15
        close_h, close_m = map(int, close_str.split(":"))
        trigger_m = close_m - buffer
        trigger_h = close_h
        if trigger_m < 0:
            trigger_m += 60
            trigger_h -= 1
        system += (
            f"\n\n⚠️  TESTING MODE: Prefer `trade_type: intraday` for any new trades this cycle."
            f" Intraday trades must be closed before {trigger_h}:{trigger_m:02d}."
        )

    return system


def _build_context(portfolio: Dict, watchlist: List[Dict], news: str,
                   risk: Dict, config: AgentConfig = None,
                   day_goal_context: str = None) -> str:
    """Build the initial context message for the LLM."""
    sym = config.currency_symbol if config else "₹"

    watchlist_lines = []
    for s in watchlist:
        ticker = s.get('ticker', '?')
        price  = s.get('current_price', 0)
        chg    = s.get('change_pct', 0)
        sma    = s.get('price_vs_sma', '?')
        rsi    = s.get('rsi_14')
        vr     = s.get('vol_ratio')
        r2res  = s.get('dist_to_resistance_pct')
        r2sup  = s.get('dist_to_support_pct')
        rsi_str = f"{rsi:>5.1f}" if rsi is not None else "  n/a"
        vr_str  = f"{vr:>4.1f}x"  if vr  is not None else " n/a"
        r2res_str = f"{r2res:>5.1f}%" if r2res is not None else "  n/a"
        r2sup_str = f"{r2sup:>5.1f}%" if r2sup is not None else "  n/a"
        watchlist_lines.append(
            f"{ticker:20s}  {sym}{price:>8.2f}  {chg:>+6.2f}%  "
            f"RSI={rsi_str}  vol={vr_str}  res={r2res_str}  sup={r2sup_str}  sma={sma}"
        )
    watchlist_text = "\n".join(watchlist_lines) if watchlist_lines else "(no data)"

    asset_label = "assets" if (config and config._market_preset and config._market_preset.market_id == "crypto") else "stocks"
    goal_section = f"\n{day_goal_context}\n" if day_goal_context else ""

    return f"""## Current Portfolio
```json
{json.dumps(portfolio, indent=2)}
```

## Risk Status
```json
{json.dumps(risk, indent=2)}
```
{goal_section}
## Full Watchlist — {len(watchlist)} {asset_label} sorted by absolute % move (biggest movers first)
RSI: <30=oversold, >70=overbought | vol: today/20d avg volume ratio (>1.5 = above-avg volume) | res/sup: % distance to 5d resistance/support
```
{"ticker":20s}  {"price":>8s}  {"chg%":>7s}  {"RSI":>9s}  {"vol":>8s}  {"res":>8s}  {"sup":>8s}  sma
{"-"*95}
{watchlist_text}
```

## Recent Market News
{news}

---

Analyze the market conditions and your portfolio. Use your tools to research
specific {asset_label} if needed, then decide whether to make any trades.

LONG signals: RSI <35 (oversold bounce), vol_ratio >1.5 (breakout volume), price near support (sup <1%),
  EMA trend bullish (ema_20 > ema_50), MACD bullish_cross.
SHORT signals: RSI >70 (overbought), price near resistance (res <0.5%), vol_ratio >1.5 (distribution),
  EMA trend bearish (ema_20 < ema_50), MACD bearish_cross.
Use `get_stock_details` to fetch full indicators (RSI, ATR, EMA20/50, MACD, VWAP) before any trade.
Caution: avoid entering longs when RSI >75 or near resistance; avoid shorts when RSI <30.
Confluence wins: a setup with 2+ aligned signals (e.g. RSI<30 + bullish EMA + MACD turning) is far stronger.
If no good opportunities exist, it's perfectly fine to hold and wait.

What trades, if any, should we make right now?"""


def _get_tool_defs(config, is_backtest: bool = False):
    """Build tool definitions with market-aware descriptions.
    During backtest, the news search tool is omitted entirely so the LLM
    can't waste round-trips calling it (it would return empty anyway)."""
    from .web_research import LLMWebSearchTool
    preset = config._market_preset
    tools = [
        LLMWebSearchTool.get_portfolio_tool(),
        LLMWebSearchTool.get_stock_detail_tool(market_preset=preset),
        LLMWebSearchTool.get_trade_tool(market_preset=preset),
    ]
    if not is_backtest:
        tools.insert(0, LLMWebSearchTool.tool_definition(market_preset=preset))
    return tools


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
        is_backtest: bool = False,
        backtest_date: str = None,
        backtest_time: str = None,
        day_goal_context: str = None,
    ) -> List[Dict]:
        """
        Run the full decision loop with tool use.
        The LLM can call tools multiple times before deciding.
        Returns list of trade actions taken.
        """
        context = _build_context(portfolio_summary, watchlist_data,
                                 news_context, risk_status, self.config,
                                 day_goal_context=day_goal_context)

        system = _format_system_prompt(self.config, learnings, is_market_open, force_intraday,
                                       is_backtest=is_backtest, backtest_date=backtest_date, backtest_time=backtest_time)

        tool_defs = _get_tool_defs(self.config, is_backtest=is_backtest)

        messages = [{"role": "user", "content": context}]
        actions_taken = []
        max_iterations = 10

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

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_uses = [b for b in assistant_content if b.type == "tool_use"]

            if not tool_uses:
                text_blocks = [b.text for b in assistant_content if b.type == "text"]
                if text_blocks:
                    logger.info(f"Agent reasoning: {' '.join(text_blocks)[:500]}")
                break

            tool_results = []
            for tool_use in tool_uses:
                result = tool_handler(tool_use.name, tool_use.input)

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
        is_backtest: bool = False,
        backtest_date: str = None,
        backtest_time: str = None,
        day_goal_context: str = None,
    ) -> List[Dict]:
        context = _build_context(portfolio_summary, watchlist_data,
                                 news_context, risk_status, self.config,
                                 day_goal_context=day_goal_context)

        system = _format_system_prompt(self.config, learnings, is_market_open, force_intraday,
                                       is_backtest=is_backtest, backtest_date=backtest_date, backtest_time=backtest_time)

        # Convert Anthropic-style tool schemas to OpenAI function format
        tool_defs = [self._to_openai_tool(t) for t in _get_tool_defs(self.config, is_backtest=is_backtest)]

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
