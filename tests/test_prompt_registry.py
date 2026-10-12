"""Prompt versions are immutable once released, and the active one is what agents render."""
import hashlib

import pytest

from agent.config import AgentConfig
from agent.decision_engine import _format_system_prompt
from agent.prompt_registry import PROMPTS_DIR, get_prompt, load_registry
from agent.session import SessionConfig

REGISTRY = load_registry()["decision"]
PLACEHOLDERS = dict(market_name="M", trading_rules="R", personality_section="P",
                    learnings="L", current_time="T", market_status="OPEN")


def _config(**session_kwargs):
    sc = SessionConfig(session_id="t", market="crypto", llm_provider="openrouter",
                       llm_model="test/model", **session_kwargs)
    sc.resolve_defaults()
    return AgentConfig.from_session(sc)


@pytest.mark.parametrize("version", sorted(REGISTRY["versions"]))
def test_released_versions_are_immutable(version):
    raw = (PROMPTS_DIR / "decision" / f"{version}.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REGISTRY["versions"][version]["sha256"], (
        f"decision/{version}.md changed after release — add a new version instead"
    )


@pytest.mark.parametrize("version", sorted(REGISTRY["versions"]))
def test_every_version_renders_with_current_code(version):
    # Any version must stay renderable, so old prompts can be re-run on the eval set.
    rendered = get_prompt("decision", version).template.format(**PLACEHOLDERS)
    assert "{" not in rendered.replace("{{", "")


def test_active_version_is_used_by_default():
    system, info = _format_system_prompt(_config(), learnings="", is_market_open=True)
    active = REGISTRY["active"]
    assert info["id"] == f"decision_{active}"
    assert info["version"] == active
    assert info["overrides_hash"] == ""
    assert system.startswith("You are an autonomous AI trading agent")


def test_session_can_pin_an_older_version():
    system, info = _format_system_prompt(_config(prompt_version="v1"), learnings="", is_market_open=True)
    assert info["id"] == "decision_v1"
    assert "Exit Discipline" not in system          # only added in v4


def test_unknown_version_falls_back_to_active():
    _, info = _format_system_prompt(_config(prompt_version="v99"), learnings="", is_market_open=True)
    assert info["version"] == REGISTRY["active"]


def test_overrides_change_the_fingerprint_not_the_version():
    _, base = _format_system_prompt(_config(), learnings="", is_market_open=True)
    _, over = _format_system_prompt(_config(extra_system_prompt="Hold winners longer."),
                                    learnings="", is_market_open=True)
    assert base["id"] == over["id"]
    assert over["overrides_hash"] and over["overrides_hash"] != base["overrides_hash"]
