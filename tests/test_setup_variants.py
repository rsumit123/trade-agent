"""Step 3: variant validation, context filters, random control, proposer input hygiene."""
import json
from datetime import date

from agent.setup_bt.engine import sample_of, simulate_day
from agent.setup_bt.proposer import build_prompt, evidence
from agent.setup_bt.setups import Day, RandomEntry, passes_filters
from agent.setup_bt.variants import log_results, parse_specs, tried_count, validate
from tests.test_setup_bt import _engine_day, _times


def test_valid_spec_builds_a_named_variant():
    s, errs = validate({"id": "gap_fade_g3_short", "base": "gap_fade", "params": {"gap_min": 3, "or_bars": 4},
                        "target_r": 2.0, "filters": {"direction": ["short"], "regime": ["trend_down", "range"],
                                                     "abs_gap_pct_min": 3}})
    assert not errs and s.name == "gap_fade_g3_short" and s.base == "gap_fade"
    assert s.params == {"gap_min": 3, "or_bars": 4} and s.target_r == 2.0
    assert isinstance(s.params["or_bars"], int)


def test_out_of_range_and_unknown_inputs_are_rejected():
    _, errs = validate({"id": "x1x", "base": "gap_fade", "params": {"gap_min": 20, "foo": 1},
                        "target_r": 9, "filters": {"regime": ["moon"], "weird_max": 1, "vix_max": "high"}})
    text = " | ".join(errs)
    for frag in ("gap_min=20", "no param 'foo'", "target_r=9", "filter regime", "unknown filter 'weird_max'",
                 "vix_max must be a number"):
        assert frag in text
    assert validate({"id": "ok_id", "base": "random_entry"})[1]          # control can't be proposed
    assert validate({"id": "Bad Id!", "base": "orb_breakout"})[1]


def test_passes_filters():
    ctx = {"regime": "range", "gap_pct": -3.2, "vix": 15, "time_bucket": "open"}
    assert passes_filters({"regime": ["range"], "abs_gap_pct_min": 3, "vix_max": 16}, ctx, "long")
    assert not passes_filters({"direction": ["short"]}, ctx, "long")
    assert not passes_filters({"rs_vs_nifty_min": 0}, ctx, "long")      # missing field rejects


class _Long:
    name, target_r, min_stop_atr, base = "f", 2.0, 0.5, "f"

    def __init__(self, filters):
        self.filters = filters

    def signal(self, d, i):
        return "long", d.c[i] - 1


def test_filters_gate_entries_in_the_engine():
    d = _engine_day([100.1] * 75, [99.9] * 75)
    assert simulate_day(d, [_Long({"time_bucket": ["open"]})], "X", {}, 10_000, 0.0, None)
    assert simulate_day(d, [_Long({"regime": ["trend_up"]})], "X", {}, 10_000, 0.0, None) == []   # no regime data


def test_random_control_is_deterministic_and_sparse():
    d = _engine_day([100.1] * 75, [99.9] * 75)
    r = RandomEntry(prob=0.2)
    a = [r.signal(d, i) for i in range(75)]
    assert a == [r.signal(d, i) for i in range(75)] and 0 < sum(1 for x in a if x) < 40


def test_three_way_split():
    assert sample_of(date(2025, 6, 1), date(2026, 5, 1), date(2025, 11, 1)) == "train"
    assert sample_of(date(2026, 1, 1), date(2026, 5, 1), date(2025, 11, 1)) == "val"
    assert sample_of(date(2026, 6, 1), date(2026, 5, 1), date(2025, 11, 1)) == "out"
    assert sample_of(date(2025, 6, 1), date(2026, 5, 1)) == "in"


def _t(setup, sample, pnl):
    return {"setup": setup, "direction": "short", "quantity": 100, "entry_price": 100.0,
            "exit_price": 100 - pnl / 100, "pnl": pnl, "sample": sample,
            "entry_context": json.dumps({"regime": "range", "time_bucket": "open", "liquidity": "high"})}


def test_proposer_never_sees_out_of_sample():
    trades = [_t("gap_fade", s, 50.0) for s in ("train", "val") for _ in range(6)]
    trades += [_t("gap_fade", "out", 999.0) for _ in range(6)]
    ev = evidence(trades)
    assert set(ev) == {"train", "val"} and "999" not in json.dumps(ev)
    prompt = build_prompt(ev, [], 5, 1)
    assert "random_entry" in prompt and "up to 5" in prompt and '"out"' not in prompt


def test_parse_specs_and_ledger(tmp_path):
    specs = parse_specs('Here:\n```json\n[{"id": "a_b", "base": "orb_breakout"}]\n```')
    assert specs == [{"id": "a_b", "base": "orb_breakout"}]
    led = tmp_path / "log.jsonl"
    log_results(led, "c1", 1, specs[0], {"train": {"n": 3}})
    log_results(led, "c2", 1, specs[0], {})
    assert tried_count(led) == 2 and tried_count(led, "c1") == 1
