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


# ── intraday-scaled risk (backtester fix) ───────────────────────────────────

from agent.setup_bt.engine import a5_series, simulate_day as _sim
from agent.setup_bt.setups import GapFade, OrbBreakout
from tests.test_setup_bt import _day


def test_a5_series_uses_only_completed_bars_and_falls_back_early():
    h, l, c = [100.5] * 20, [99.5] * 20, [100.0] * 20
    out = a5_series(h, l, c, daily_atr=2.4)
    assert abs(out[0] - 0.2) < 1e-9 and abs(out[5] - 0.2) < 1e-9   # < 7 bars: daily ATR / 12
    assert abs(out[10] - 1.0) < 1e-9                # true range of each bar is 1.0


def test_orb_takes_only_the_first_break_and_a_tradable_range():
    closes = [100, 100.4, 100.2, 100.3, 101.6, 101.7, 101.9] + [101.9] * 20
    vols = [1000] * 4 + [3000, 3000, 3000] + [1000] * 20
    d = _day(closes, vols=vols)
    orb = OrbBreakout()
    assert orb.signal(d, 4) is not None             # first break
    assert orb.signal(d, 5) is None and orb.signal(d, 6) is None   # already outside the range: not the first break
    wide = _day([100, 104, 96, 103, 104.5] + [104.5] * 20, vols=[1000] * 4 + [3000] + [1000] * 20)
    assert OrbBreakout().signal(wide, 4) is None    # opening range ~8% wide: not tradable


def test_stop_is_sized_from_the_5_minute_atr_and_wide_structural_stops_are_skipped():
    d = _engine_day([100.1] * 75, [99.9] * 75)
    d.a5 = [0.5] * 75                                # 5-minute ATR 0.5 -> floor 3 x 0.5 = 1.5 (1.5% of 100)

    class Tight:
        name, base, filters, target_r, min_stop_a5, max_stop_a5 = "t", "t", {}, 2.0, 3.0, 12.0

        def signal(self, d, i):
            return "long", d.c[i] - 0.2              # structural stop 0.2 away: widened to 1.5

    class Wide(Tight):
        def signal(self, d, i):
            return "long", d.c[i] - 10.0             # 10 away = 20 x ATR: skipped

    (t,) = _sim(d, [Tight()], "X", {}, 10_000, 0.0, None)
    assert t["stop"] == 98.5 and t["target"] == 103.0
    assert _sim(d, [Wide()], "X", {}, 10_000, 0.0, None) == []


def test_floor_is_at_least_0_35_percent_of_price():
    d = _engine_day([100.1] * 75, [99.9] * 75)
    d.a5 = [0.05] * 75                               # 3 x 0.05 = 0.15, but 0.35% of 100 = 0.35

    class S:
        name, base, filters, target_r, min_stop_a5, max_stop_a5 = "s", "s", {}, 2.0, 3.0, 12.0

        def signal(self, d, i):
            return "short", d.c[i] + 0.1

    (t,) = _sim(d, [S()], "X", {}, 10_000, 0.0, None)
    assert abs(t["stop"] - 100.35) < 1e-9


def test_proposer_accepts_the_new_params_and_mentions_notional():
    s, errs = validate({"id": "orb_narrow", "base": "orb_breakout",
                        "params": {"min_range_pct": 0.5, "max_range_pct": 2.0}, "min_stop_a5": 2.5, "max_stop_a5": 10})
    assert not errs and s.min_stop_a5 == 2.5 and s.max_stop_a5 == 10
    assert validate({"id": "vwap_x", "base": "vwap_cross", "params": {"stop_atr": 0.5}})[1]   # old param is gone
    prompt = build_prompt({"train": {}, "val": {}}, [], 5, 1, notional=100_000)
    assert "Rs 100,000" in prompt and "5-minute ATR" in prompt and "15:15 close, so that mistake" in prompt
