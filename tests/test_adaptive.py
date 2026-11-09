"""Regime calibration of the entry thresholds (session: adaptive_rules)."""
from types import SimpleNamespace as NS

import agent.adaptive as ad
import agent.session as session_mod
from agent.config import AgentConfig
from agent.learner import Learner
from agent.portfolio import Portfolio
from agent.session import SessionConfig


def _mkt(chg, pos, up, down):
    return {"indices": [{"name": "NIFTY 50", "chg_open_pct": chg, "range_pos_pct": pos}],
            "breadth": {"up_open": up, "down_open": down}}


def test_confirmed_uptrend_relaxes_the_thresholds_for_longs_only():
    cal = ad.calibrate(_mkt(0.9, 88, 38, 12))
    assert cal["regime"] == "trend_up" and cal["with_trend"] == "up"
    assert (cal["min_rr_with"], cal["vwap_atr_with"]) == (1.2, 0.7)
    assert (cal["min_rr_other"], cal["vwap_atr_other"]) == (1.6, 0.35)
    text = ad.format_calibration(cal)
    assert "Longs (with the trend): minimum reward:risk 1.2" in text and "replace any minimum reward:risk" in text


def test_downtrend_relaxes_shorts():
    cal = ad.calibrate(_mkt(-0.8, 12, 9, 41))
    assert cal["regime"] == "trend_down" and "Shorts (with the trend)" in ad.format_calibration(cal)


def test_index_move_without_breadth_is_not_a_trend_day():
    cal = ad.calibrate(_mkt(0.9, 88, 20, 30))          # Nifty up on heavyweights, most stocks down
    assert cal["regime"] == "range" and cal["min_rr_with"] == 1.6 and cal["with_trend"] is None
    assert "no confirmed trend" in ad.format_calibration(cal)


def test_flat_market_keeps_the_strict_numbers_and_missing_data_is_silent():
    cal = ad.calibrate(_mkt(0.1, 50, 25, 25))
    assert (cal["min_rr_with"], cal["vwap_atr_with"]) == (1.6, 0.35)
    assert ad.calibrate(None) is None and ad.calibrate({"indices": []}) is None
    assert ad.format_calibration(None) == ""
    assert ad.RR_BOUNDS[0] <= ad.TREND_RR <= ad.RR_BOUNDS[1] and ad.VWAP_ATR_BOUNDS[0] <= ad.TREND_VWAP_ATR <= ad.VWAP_ATR_BOUNDS[1]


def test_flag_survives_yaml_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    session_mod.save_session(SessionConfig(session_id="t", market="nse-intraday", adaptive_rules=True))
    assert session_mod.load_session("t").adaptive_rules is True
    assert SessionConfig(session_id="u", market="nse").adaptive_rules is False


class _LLM:
    def __init__(self):
        self.calls, self.messages = [], self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(type="text", text="- a\n- b\n- c")], stop_reason="end_turn")


def test_learner_stops_writing_numeric_thresholds_for_adaptive_sessions(tmp_path):
    def run(**session):
        tmp_path.mkdir(exist_ok=True)
        sc = SessionConfig(session_id="t", market="nse-intraday", llm_provider="anthropic", llm_model="m", **session)
        sc.resolve_defaults()
        cfg = AgentConfig.from_session(sc)
        cfg.db_path, cfg.learnings_path = str(tmp_path / "t.db"), str(tmp_path / "j.md")
        pf = Portfolio(cfg.db_path, 100000.0)
        t = pf.execute_buy("AAA.NS", 10, 100.0, "intraday")
        pf.execute_sell(t.id, 101.0, exit_type="target_hit")
        lr = Learner(cfg, pf)
        with open(lr.journal_path, "a") as f:
            f.write("### entry\n" + "x" * 600 + "\n")
        llm = _LLM()
        lr.update_distilled_rules(llm)
        return llm.calls[0]["messages"][0]["content"]
    assert "Do NOT write numeric thresholds" in run(exploration_mandate=False, adaptive_rules=True)
    assert "Do NOT write numeric thresholds" not in run(exploration_mandate=False)


def test_runner_appends_the_calibration_only_for_adaptive_sessions(monkeypatch):
    import agent.market_context as mc
    from agent.market_presets import get_preset
    from agent.runner import TradingAgent
    ctx = {"indices": [{"name": "NIFTY 50", "last": 23000.0, "chg_prev_pct": 1.0, "chg_open_pct": 0.9, "high": 23100.0,
                        "low": 22800.0, "range_pos_pct": 88, "trend_pct": 0.1}],
           "nifty_chg_open_pct": 0.9, "breadth": {"up_open": 38, "down_open": 12, "up_prev": 40, "down_prev": 10}}
    monkeypatch.setattr(mc, "fetch", lambda md, n50: ctx)
    monkeypatch.setattr("agent.sector_data.get_nifty50", lambda: [])

    def agent(**session):
        sc = SessionConfig(session_id="t", market="nse-intraday", **session)
        sc.resolve_defaults()
        a = TradingAgent.__new__(TradingAgent)
        a.config, a.preset, a._is_backtest = AgentConfig.from_session(sc), get_preset("nse-intraday"), False
        a.market_data = NS(_call_with_retry=lambda *x, **k: None)
        return a

    assert "Today's calibration" in agent(adaptive_rules=True)._market_context([], False)
    assert "Today's calibration" not in agent()._market_context([], False)
