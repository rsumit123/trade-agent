from types import SimpleNamespace

from agent.config import AgentConfig
from agent.decision_engine import AnthropicDecisionEngine, _with_cache_breakpoints
from agent.llm_pricing import estimate_usd


def test_breakpoints_mark_system_and_newest_message_only():
    msgs = [{"role": "user", "content": "context"},
            {"role": "assistant", "content": ["tool call"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}]}]
    system, sent = _with_cache_breakpoints("rules", msgs)
    assert system == [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
    assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in msgs[-1]["content"][-1]       # history isn't mutated
    assert sent[0] is msgs[0]


def test_cycle_sends_cache_marks_and_counts_cached_tokens():
    calls = []
    usage = SimpleNamespace(input_tokens=50, output_tokens=10,
                            cache_creation_input_tokens=1000, cache_read_input_tokens=4000)
    replies = iter([
        SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t1", name="get_portfolio_status", input={})],
                        usage=usage, stop_reason="tool_use"),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], usage=usage, stop_reason="end_turn"),
    ])

    def create(**kw):
        calls.append(kw)
        return next(replies)

    eng = AnthropicDecisionEngine.__new__(AnthropicDecisionEngine)
    eng.config = AgentConfig(starting_capital=500_000)
    eng.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    eng.run_decision_loop({}, [], "", {}, "", [], lambda name, inp: {"ok": True})

    assert len(calls) == 2
    for kw in calls:
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kw["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert eng.last_usage["cache_read_tokens"] == 8000
    assert eng.last_usage["cache_write_tokens"] == 2000


def test_cached_tokens_are_priced():
    # Opus: $4/M input. 1M cache reads cost 0.1x, 1M cache writes 1.25x.
    assert round(estimate_usd("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000), 2) == 0.40
    assert round(estimate_usd("claude-opus-5-5", 0, 0, cache_write_tokens=1_000_000), 2) == 5.00
