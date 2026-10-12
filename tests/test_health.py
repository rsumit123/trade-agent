"""An agent whose LLM calls all fail must be reported as failing, not 'ok'."""
import agent.health as health
from tests.test_tracing import _engine, _run


def _record(tmp_path, failed, error=None):
    return health.record_cycle(tmp_path, "vin", "openai/gpt-oss-120b", failed, error)


def test_engine_exposes_llm_error(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    eng = _engine(fail=True)
    actions, _ = _run(eng)
    assert actions == []
    assert "upstream 503" in eng.last_error
    assert eng.last_usage["calls"] == 0


def test_engine_clears_error_on_success(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    eng = _engine()
    eng.last_error = "stale error from a previous cycle"
    _run(eng)
    assert eng.last_error is None


def test_alerts_once_after_threshold_then_recovers(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(health, "send_telegram", lambda text: sent.append(text) or True)

    for _ in range(health.ALERT_AFTER - 1):
        _record(tmp_path, True, "404 model unavailable")
    assert sent == []                                   # below threshold: no alert

    h = _record(tmp_path, True, "404 model unavailable")
    assert h["consecutive_llm_failures"] == health.ALERT_AFTER
    assert len(sent) == 1 and "can't reach its LLM" in sent[0] and "404" in sent[0]

    _record(tmp_path, True, "404 model unavailable")
    assert len(sent) == 1                               # no repeat alerts while still down

    h = _record(tmp_path, False)
    assert h["consecutive_llm_failures"] == 0 and h["alerted"] is False
    assert len(sent) == 2 and "recovered" in sent[1]


def test_no_recovery_message_without_prior_alert(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(health, "send_telegram", lambda text: sent.append(text) or True)
    _record(tmp_path, True, "blip")
    _record(tmp_path, False)
    assert sent == []


def test_health_persists_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "send_telegram", lambda text: False)
    _record(tmp_path, True, "boom")
    h = health.read_health(tmp_path)
    assert h["consecutive_llm_failures"] == 1 and h["last_error"] == "boom"


def test_telegram_is_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert health.send_telegram("hi") is False
