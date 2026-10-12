"""Tracing must be a transparent no-op when off, and produce a sane span tree when on."""
import json
from types import SimpleNamespace

import pytest

import agent.tracing as tracing
from agent.config import AgentConfig
from agent.decision_engine import OpenRouterDecisionEngine
from agent.session import SessionConfig


# ── Fake OpenAI-compatible client: one tool call, then a final answer ──

def _tool_call_msg():
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="place_trade",
                                 arguments=json.dumps({"ticker": "BTC-USD", "action": "BUY"})),
    )
    return SimpleNamespace(content="Buying BTC on breakout.", tool_calls=[tc])


def _final_msg():
    return SimpleNamespace(content="Done for this cycle.", tool_calls=None)


class _FakeCompletions:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("upstream 503")
        self.calls += 1
        msg = _tool_call_msg() if self.calls == 1 else _final_msg()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def _engine(fail=False):
    sc = SessionConfig(session_id="t_sess", market="crypto",
                       llm_provider="openrouter", llm_model="test/model")
    sc.resolve_defaults()
    config = AgentConfig.from_session(sc)
    eng = OpenRouterDecisionEngine.__new__(OpenRouterDecisionEngine)
    eng.config = config
    eng.last_reasoning, eng.last_usage = [], {}
    eng.model = "test/model"
    eng.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(fail)))
    return eng


def _run(eng):
    handled = []

    def handler(name, inp):
        handled.append(name)
        return {"status": "filled", "ticker": inp.get("ticker")}

    actions = eng.run_decision_loop(
        portfolio_summary={"cash": 10000, "positions": [], "total_value": 10000},
        watchlist_data=[], news_context="", risk_status={}, learnings="",
        tools=[], tool_handler=handler,
    )
    return actions, handled


@pytest.fixture
def tracing_off(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_init_done", False)


@pytest.fixture(scope="module")
def _langfuse_memory():
    # Langfuse clients are singletons per public key, so build one per module.
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client = Langfuse(public_key="pk-test", secret_key="sk-test",
                      base_url="http://localhost:1", tracer_provider=provider)
    yield client, exporter
    provider.shutdown()


@pytest.fixture
def tracing_on(monkeypatch, _langfuse_memory):
    client, exporter = _langfuse_memory
    exporter.clear()
    monkeypatch.setattr(tracing, "_client", client)
    monkeypatch.setattr(tracing, "_init_done", True)
    return exporter


def test_disabled_without_keys(tracing_off):
    assert tracing.is_enabled() is False


def test_engine_unchanged_when_tracing_off(tracing_off):
    actions, handled = _run(_engine())
    assert handled == ["place_trade"]
    assert len(actions) == 1 and actions[0]["input"]["ticker"] == "BTC-USD"


def test_span_tree_when_tracing_on(tracing_on):
    actions, _ = _run(_engine())
    assert len(actions) == 1

    spans = tracing_on.get_finished_spans()
    by_name = {s.name: s for s in spans}
    assert {"decision_cycle", "llm_call_0", "llm_call_1", "place_trade"} <= set(by_name)

    root = by_name["decision_cycle"]
    for child in ("llm_call_0", "llm_call_1", "place_trade"):
        assert by_name[child].parent.span_id == root.context.span_id
        assert by_name[child].context.trace_id == root.context.trace_id

    assert root.attributes["session.id"] == "t_sess"
    assert "crypto" in root.attributes["langfuse.trace.tags"]
    gen = by_name["llm_call_0"]
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert json.loads(gen.attributes["langfuse.observation.usage_details"]) == {"input": 100, "output": 20}


def test_llm_failure_still_closes_cycle(tracing_on):
    actions, handled = _run(_engine(fail=True))
    assert actions == [] and handled == []
    names = [s.name for s in tracing_on.get_finished_spans()]
    assert "decision_cycle" in names and "llm_call_0" in names


def test_broken_tracer_never_breaks_trading(monkeypatch):
    class Boom:
        def start_as_current_observation(self, **kw):
            raise RuntimeError("langfuse down")

    monkeypatch.setattr(tracing, "_client", Boom())
    monkeypatch.setattr(tracing, "_init_done", True)
    actions, handled = _run(_engine())
    assert handled == ["place_trade"] and len(actions) == 1
