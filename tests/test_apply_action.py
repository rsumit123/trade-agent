"""The review loop's only write path must reject anything outside the allowlist."""
import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parent.parent / ".claude/skills/alphaagent-review/scripts/apply_action.py"
_spec = importlib.util.spec_from_file_location("apply_action", _path)
apply_action = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_action)


def _ok(action, market="crypto"):
    return apply_action.validate(action, market)


def _rejected(action, market="crypto"):
    with pytest.raises(SystemExit):
        apply_action.validate(action, market)


def test_valid_actions():
    assert _ok({"session": "btc_haiku", "action": "set_interval", "value": 30}) == 30
    assert _ok({"session": "btc_haiku", "action": "set_include_news", "value": False}) is False
    assert _ok({"session": "btc_haiku", "action": "set_universe", "value": ["btc-usd", " ETH-USD "]}) == ["BTC-USD", "ETH-USD"]
    assert _ok({"session": "btc_haiku", "action": "set_universe", "value": []}) == []
    assert _ok({"session": "nse_default", "action": "set_universe", "value": ["TATASTEEL.NS"]}, "nse") == ["TATASTEEL.NS"]
    assert _ok({"session": "btc_haiku", "action": "set_extra_system_prompt", "value": "Hold winners."}) == "Hold winners."
    assert _ok({"session": "btc_haiku", "action": "restart"}) is None


@pytest.mark.parametrize("action", [
    {"session": "btc_haiku", "action": "delete_session"},
    {"session": "btc_haiku", "action": "deploy"},
    {"session": "btc_haiku", "action": "set_tier", "value": "admin"},
    {"session": "btc_haiku", "action": "set_interval", "value": 1},
    {"session": "btc_haiku", "action": "set_interval", "value": "30"},
    {"session": "btc_haiku", "action": "set_interval", "value": True},
    {"session": "btc_haiku", "action": "set_include_news", "value": "false"},
    {"session": "btc_haiku", "action": "set_extra_system_prompt", "value": "x" * 5000},
    {"session": "btc_haiku", "action": "restart", "value": "now"},
    {"session": "btc_haiku", "action": "stop", "extra": "rm -rf"},
])
def test_rejects_out_of_allowlist(action):
    _rejected(action)


def test_market_isolation():
    _rejected({"session": "nse_default", "action": "set_universe", "value": ["BTC-USD"]}, "nse")
    _rejected({"session": "nse_sonnet", "action": "set_universe", "value": ["RELIANCE.NS", "ETH-USD"]}, "nse-intraday")
    _rejected({"session": "btc_haiku", "action": "set_universe", "value": ["RELIANCE.NS"]}, "crypto")


def test_only_production_sessions():
    assert "nb6" not in apply_action.ALLOWED_SESSIONS
    assert "intra_6" not in apply_action.ALLOWED_SESSIONS
