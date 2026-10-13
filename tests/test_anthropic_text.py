"""Claude 5.x replies start with a thinking block; reflections must still get the text."""
from types import SimpleNamespace as NS

import pytest

from agent.anthropic_text import response_text, thinking_budget
from agent.config import AgentConfig
from agent.learner import Learner
from agent.portfolio import Portfolio


def _resp(*blocks, stop_reason="end_turn"):
    return NS(content=list(blocks), stop_reason=stop_reason)


THINKING = NS(type="thinking", thinking="")
TEXT = NS(type="text", text="  Stop held; target too far.  ")


def test_skips_thinking_block():
    assert response_text(_resp(THINKING, TEXT)) == "Stop held; target too far."


def test_thinking_only_response_raises():
    # Seen in prod: Haiku 5.5 at max_tokens=300 returned only a thinking block.
    with pytest.raises(ValueError, match="max_tokens"):
        response_text(_resp(THINKING, stop_reason="max_tokens"))


def test_budget_leaves_room_for_thinking():
    assert thinking_budget(300) >= 2000


class _FakeAnthropic:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return _resp(THINKING, TEXT)


def test_trade_reflection_uses_text_block(tmp_path):
    cfg = AgentConfig(llm_provider="anthropic")
    learner = Learner.__new__(Learner)
    learner.config = cfg
    trade = NS(ticker="ASHOKLEY.NS", trade_type="intraday", direction="long", action="BUY",
               quantity=600, entry_price=147.41, exit_price=146.73, reason="oversold bounce",
               exit_reason="stop hit", entry_time="2026-10-12T04:06:05", exit_time="2026-10-12T05:27:27",
               conviction=4, stop_price=146.8, target_price=148.8, exit_type="stop_hit")
    client = _FakeAnthropic()
    out = learner._generate_trade_reflection(trade, -408.0, -0.46, "1h 21m", client)
    assert out == "Stop held; target too far."
    assert client.calls[0]["max_tokens"] >= 2000
