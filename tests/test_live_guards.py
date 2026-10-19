"""Real-money guards for the live session: no exploration mandate (prompt v5 +
learner), and an ATR stop floor on update_levels (session: min_stop_atr)."""
from types import SimpleNamespace as NS

import pytest

import agent.session as session_mod
from agent.config import AgentConfig
from agent.decision_engine import _build_trading_rules, _format_system_prompt, _get_tool_defs
from agent.learner import Learner
from agent.market_presets import get_preset
from agent.portfolio import Portfolio
from agent.prompt_registry import get_prompt
from agent.risk_manager import RiskManager
from agent.runner import TradingAgent
from agent.session import SessionConfig

EXPLORE = ("Exploration is required", "Sitting in cash for many cycles is a failure mode",
           "Exploration budget", "Keep sampling")


def _cfg(**session):
    sc = SessionConfig(session_id="t", market="nse-intraday", llm_provider="anthropic",
                       llm_model="m", **session)
    sc.resolve_defaults()
    return AgentConfig.from_session(sc)


# ── prompt ───────────────────────────────────────────────────────────────────

def test_v5_drops_the_exploration_mandate_and_v4_keeps_it():
    v4, v5 = get_prompt("decision", "v4").template, get_prompt("decision", "v5").template
    assert "Exploration is required" in v4 and "failure mode, not a virtue" in v4
    assert "Exploration is required" not in v5 and "failure mode, not a virtue" not in v5
    assert "An untested setup is not a reason to trade" in v5
    assert "Sample sizes matter" in v5          # the sample-size caution stays


def test_live_session_pinned_to_v5_renders_without_exploration_text():
    system, info = _format_system_prompt(_cfg(prompt_version="v5"), learnings="L", is_market_open=True)
    assert info["version"] == "v5"
    assert not any(s in system for s in EXPLORE[:2])
    system, info = _format_system_prompt(_cfg(), learnings="L", is_market_open=True)
    assert info["version"] == "v4" and "Exploration is required" in system   # paper default unchanged


def test_stop_floor_replaces_the_tight_percentage_stop_rule():
    preset = get_preset("nse-intraday")
    assert "-0.3% to -0.5%" in _build_trading_rules(preset)
    rules = _build_trading_rules(preset, 0.5)
    assert "0.5×ATR(14)" in rules and "-0.3% to -0.5%" not in rules
    system, _ = _format_system_prompt(_cfg(min_stop_atr=0.5), learnings="L", is_market_open=True)
    assert "at least 0.5×ATR(14)" in system


def test_update_levels_tool_mentions_the_floor_only_when_set():
    def desc(cfg):
        return next(t for t in _get_tool_defs(cfg) if t["name"] == "update_levels")["description"]
    assert "ATR" not in desc(_cfg())
    assert "0.5×ATR(14)" in desc(_cfg(min_stop_atr=0.5))


def test_new_fields_survive_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    session_mod.save_session(SessionConfig(session_id="t", market="nse-intraday",
                                           exploration_mandate=False, min_stop_atr=0.5))
    sc = session_mod.load_session("t")
    assert sc.exploration_mandate is False and sc.min_stop_atr == 0.5
    assert SessionConfig(session_id="u", market="nse").exploration_mandate is True


# ── learner ──────────────────────────────────────────────────────────────────

class _LLM:
    def __init__(self):
        self.calls, self.messages = [], self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(type="text", text="- r1\n- r2\n- r3")], stop_reason="end_turn")


def _learner(tmp_path, **session):
    tmp_path.mkdir(exist_ok=True)
    cfg = _cfg(**session)
    cfg.db_path, cfg.learnings_path = str(tmp_path / "t.db"), str(tmp_path / "journal.md")
    pf = Portfolio(cfg.db_path, 100000.0)
    t = pf.execute_buy("AAA.NS", 10, 100.0, "intraday", reason="thesis")
    pf.execute_sell(t.id, 101.0, reason="done", exit_type="target_hit")
    l = Learner(cfg, pf)
    with open(l.journal_path, "a") as f:
        f.write("### entry\n" + "x" * 600 + "\n")
    return l


def test_rules_prompt_keeps_exploration_for_paper_and_drops_it_for_live(tmp_path):
    llm = _LLM()
    _learner(tmp_path).update_distilled_rules(llm)
    paper = llm.calls[0]["messages"][0]["content"]
    assert "Exploration budget" in paper and "Keep sampling" in paper

    llm = _LLM()
    _learner(tmp_path / "live", exploration_mandate=False).update_distilled_rules(llm)
    live = llm.calls[0]["messages"][0]["content"]
    assert "Exploration budget" not in live and "Keep sampling" not in live
    assert "trades real money" in live and "Remove such rules" in live


# ── stop floor ───────────────────────────────────────────────────────────────

class _Market:
    def __init__(self, prices, atr=None):
        self.prices, self.atr = prices, atr

    def get_current_prices(self, tickers=None):
        return {t: self.prices[t] for t in tickers if t in self.prices}

    def get_stock_context(self, ticker, include_vwap=False):
        return {"atr_14": self.atr} if self.atr else {}


def _agent(tmp_path, prices, min_stop_atr=0.5, ctx_atr=None):
    tmp_path.mkdir(exist_ok=True)
    cfg = _cfg(min_stop_atr=min_stop_atr) if min_stop_atr else _cfg()
    a = TradingAgent.__new__(TradingAgent)
    a.config, a.preset, a.session = cfg, cfg._market_preset, None
    a.portfolio = Portfolio(str(tmp_path / "t.db"), 500_000)
    a.risk_manager = RiskManager(cfg, a.portfolio)
    a.market_data = _Market(prices, ctx_atr)
    a._auto_exits, a._is_backtest = [], False
    return a


def test_long_stop_inside_the_band_is_moved_out_and_reported(tmp_path):
    a = _agent(tmp_path, {"IXIGO.NS": 160.0})
    t = a.portfolio.execute_buy("IXIGO.NS", 62, 160.71, "intraday", stop_price=150.25, atr_14=7.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 159.2, "reason": "vwap"})
    assert r["success"] and r["stop_price"] == 156.5           # 160 - 0.5×7
    assert "moved from 159.2 to 156.5" in r["note"]
    assert a.portfolio.get_open_positions()[0].stop_price == 156.5


def test_short_stop_inside_the_band_is_moved_out(tmp_path):
    a = _agent(tmp_path, {"KROSS.NS": 304.0})
    t = a.portfolio.execute_short("KROSS.NS", 32, 308.0, stop_price=334.25, atr_14=17.5)
    r = a._update_levels({"trade_id": t.id, "stop_price": 309.0, "reason": "trail"})
    assert r["stop_price"] == 312.75 and "note" in r               # 304 + 0.5×17.5


def test_stop_outside_the_band_passes_untouched_including_a_breakeven_trail(tmp_path):
    a = _agent(tmp_path, {"SGMART.NS": 760.0})
    t = a.portfolio.execute_short("SGMART.NS", 10, 784.0, stop_price=840.0, atr_14=37.0)
    r = a._update_levels({"trade_id": t.id, "stop_price": 784.0, "reason": "breakeven"})
    assert r["stop_price"] == 784.0 and "note" not in r            # 784 ≥ 760 + 18.5


def test_target_only_updates_and_unset_floor_are_not_touched(tmp_path):
    a = _agent(tmp_path, {"IXIGO.NS": 160.0})
    t = a.portfolio.execute_buy("IXIGO.NS", 62, 160.71, "intraday", stop_price=150.25, atr_14=7.0)
    r = a._update_levels({"trade_id": t.id, "target_price": 164.6, "reason": "t"})
    assert r["target_price"] == 164.6 and r["stop_price"] == 150.25
    b = _agent(tmp_path / "off", {"IXIGO.NS": 160.0}, min_stop_atr=None)
    t2 = b.portfolio.execute_buy("IXIGO.NS", 62, 160.71, "intraday", stop_price=150.25, atr_14=7.0)
    assert b._update_levels({"trade_id": t2.id, "stop_price": 159.2, "reason": "r"})["stop_price"] == 159.2


def test_missing_atr_falls_back_to_stock_context_then_skips(tmp_path):
    a = _agent(tmp_path, {"PNB.NS": 123.5}, ctx_atr=2.0)
    t = a.portfolio.execute_buy("PNB.NS", 80, 123.93, "intraday", stop_price=120.0)   # pre-migration trade: no atr
    assert a._update_levels({"trade_id": t.id, "stop_price": 123.25, "reason": "r"})["stop_price"] == 122.5
    b = _agent(tmp_path / "noatr", {"PNB.NS": 123.5})
    t2 = b.portfolio.execute_buy("PNB.NS", 80, 123.93, "intraday", stop_price=120.0)
    assert b._update_levels({"trade_id": t2.id, "stop_price": 123.25, "reason": "r"})["stop_price"] == 123.25


def test_atr_is_persisted_on_the_trade(tmp_path):
    pf = Portfolio(str(tmp_path / "t.db"), 100000.0)
    t = pf.execute_short("KROSS.NS", 1, 308.0, atr_14=17.5)
    assert pf.get_open_positions()[0].atr_14 == 17.5
    assert pf.execute_buy("AAA.NS", 1, 100.0, "intraday").atr_14 is None
