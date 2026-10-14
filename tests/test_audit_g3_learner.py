"""Learner audit fixes: short P&L sign, truncated rules, IST stamps, exit_type stats,
no-activity skips, closed-today review window, all-time stats."""
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from agent.config import AgentConfig
from agent.learner import Learner
from agent.portfolio import Portfolio

IST = ZoneInfo("Asia/Kolkata")


class FakeLLM:
    """Anthropic-style client that records prompts and returns a canned reply."""

    def __init__(self, text="- a\n- b\n- c\n", stop_reason="end_turn"):
        self.calls = []
        self.messages = self
        self.text = text
        self.stop_reason = stop_reason

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(type="text", text=self.text)], stop_reason=self.stop_reason)


class FakeOpenAI:
    def __init__(self, text, finish_reason):
        self.calls = []
        self.chat = NS(completions=self)
        self._text, self._finish = text, finish_reason

    def create(self, **kw):
        self.calls.append(kw)
        return NS(choices=[NS(message=NS(content=self._text), finish_reason=self._finish)])


def _learner(tmp_path, provider="anthropic", tz="Asia/Kolkata", is_24x7=False):
    cfg = AgentConfig(llm_provider=provider, timezone=tz, db_path=str(tmp_path / "t.db"),
                      learnings_path=str(tmp_path / "journal.md"))
    cfg._market_preset = NS(is_24x7=is_24x7, currency_symbol="₹")
    pf = Portfolio(cfg.db_path, 100000.0)
    return Learner(cfg, pf), pf


def _set_times(pf, trade_id, entry=None, exit_=None):
    with sqlite3.connect(pf.db_path) as c:
        if entry:
            c.execute("UPDATE trades SET entry_time=? WHERE id=?", (entry.isoformat(), trade_id))
        if exit_:
            c.execute("UPDATE trades SET exit_time=? WHERE id=?", (exit_.isoformat(), trade_id))


def _closed(pf, ticker="AAA.NS", buy=100.0, sell=101.0, trade_type="intraday", exit_type=None, reason=""):
    t = pf.execute_buy(ticker, 10, buy, trade_type=trade_type, reason="thesis")
    return pf.execute_sell(t.id, sell, reason=reason, exit_type=exit_type)


def _big_journal(l):
    with open(l.journal_path, "a") as f:
        f.write("### entry\n" + "x" * 600 + "\n")


# ── 1. short P&L sign + direction ──────────────────────────────────────────

def test_short_exit_pnl_pct_sign_and_direction(tmp_path):
    l, pf = _learner(tmp_path)
    s = pf.execute_short("AAA.NS", 10, 100.0, reason="fade")
    closed = pf.execute_cover(s.id, 99.0, reason="target", exit_type="target_hit")
    llm = FakeLLM("- ok")
    l.write_trade_log(closed, llm)
    journal = l.journal_path.read_text()
    assert "SHORT 10x AAA.NS" in journal
    assert "(+1.00%)" in journal           # price fell 1% -> +1% for a short
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "Direction: SHORT" in prompt and "(+1.00%)" in prompt


def test_long_exit_unchanged(tmp_path):
    l, pf = _learner(tmp_path)
    closed = _closed(pf, buy=100.0, sell=99.0)
    l.write_trade_log(closed, None)
    j = l.journal_path.read_text()
    assert "LONG 10x AAA.NS" in j and "(-1.00%)" in j


# ── 2/3. distilled rules ───────────────────────────────────────────────────

def _with_activity(l, pf):
    _closed(pf)
    _big_journal(l)


def test_truncated_rules_keep_existing_block(tmp_path):
    l, pf = _learner(tmp_path)
    _with_activity(l, pf)
    good = "## 📌 Distilled Rules\n\n- keep me 1\n- keep me 2\n- keep me 3"
    l.journal_path.write_text(l.journal_path.read_text().replace("---\n\n", f"---\n\n{good}\n\n---\n\n", 1))
    before = l.journal_path.read_text()
    l.update_distilled_rules(FakeLLM("- cut off mid sen", stop_reason="max_tokens"))
    assert l.journal_path.read_text() == before


def test_truncated_openai_finish_reason_length_keeps_block(tmp_path):
    l, pf = _learner(tmp_path, provider="openrouter")
    _with_activity(l, pf)
    before = l.journal_path.read_text()
    l.update_distilled_rules(FakeOpenAI("- a\n- b\n- c\n- d", "length"))
    assert l.journal_path.read_text() == before


def test_too_few_bullets_rejected(tmp_path):
    l, pf = _learner(tmp_path)
    _with_activity(l, pf)
    before = l.journal_path.read_text()
    l.update_distilled_rules(FakeLLM("- only one"))
    assert l.journal_path.read_text() == before


def test_rules_regenerated_with_previous_rules_and_big_budget(tmp_path):
    l, pf = _learner(tmp_path)
    _with_activity(l, pf)
    old = "## 📌 Distilled Rules\n\n- OLD-LESSON-XYZ"
    l.journal_path.write_text(l.journal_path.read_text().replace("---\n\n", f"---\n\n{old}\n\n---\n\n", 1))
    llm = FakeLLM("- new 1\n- new 2\n- new 3")
    l.update_distilled_rules(llm)
    assert "OLD-LESSON-XYZ" in llm.calls[0]["messages"][0]["content"]
    assert llm.calls[0]["max_tokens"] >= 4000
    j = l.journal_path.read_text()
    assert "- new 1" in j and "OLD-LESSON-XYZ" not in j
    assert j.count("## 📌 Distilled Rules") == 1


# ── 4. timestamps ──────────────────────────────────────────────────────────

def test_journal_stamp_is_ist_labelled(tmp_path):
    l, pf = _learner(tmp_path)
    t = pf.execute_buy("AAA.NS", 1, 100.0, "intraday", reason="r")
    l.write_trade_log(t)
    line = [x for x in l.journal_path.read_text().splitlines() if x.startswith("### ")][0]
    stamp = line[4:].split(" — ")[0]
    assert stamp.endswith(" IST")
    parsed = datetime.strptime(stamp[:-4], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    assert abs(datetime.now(IST) - parsed) < timedelta(minutes=2)


def test_crypto_stamp_is_utc(tmp_path):
    l, pf = _learner(tmp_path, tz="UTC", is_24x7=True)
    t = pf.execute_buy("BTC-USD", 1, 100.0, "intraday", reason="r")
    l.write_trade_log(t)
    assert " UTC — Entry" in l.journal_path.read_text()


# ── 5. exit_type stats ─────────────────────────────────────────────────────

def test_stop_target_stats_use_exit_type_not_free_text(tmp_path):
    l, pf = _learner(tmp_path)
    _closed(pf, exit_type="target_hit", reason="stop was too tight so I took profit")
    _closed(pf, buy=100, sell=98, exit_type="stop_hit", reason="reversal")
    _closed(pf, exit_type="manual", reason="target stop target")
    out = l._get_stats_by_category()
    assert "Stop hits :   1 trades" in out
    assert "Target hits:   1 trades" in out


# ── 6. skip with no activity ───────────────────────────────────────────────

def test_no_activity_today_skips_review_and_rules(tmp_path):
    l, pf = _learner(tmp_path)
    t = _closed(pf)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    _set_times(pf, t.id, entry=old, exit_=old)
    _big_journal(l)
    llm = FakeLLM()
    assert l.generate_daily_review(llm) == ""
    l.update_distilled_rules(llm)
    assert llm.calls == []


# ── 7. closed-today window ─────────────────────────────────────────────────

def test_review_includes_swing_closed_today_and_labels_open(tmp_path):
    l, pf = _learner(tmp_path)
    swing = _closed(pf, ticker="SWING.NS", trade_type="swing")
    _set_times(pf, swing.id, entry=datetime.now(timezone.utc) - timedelta(days=3))   # exit stays now
    stale = _closed(pf, ticker="OLD.NS")
    yesterday = datetime.now(timezone.utc) - timedelta(days=2)
    _set_times(pf, stale.id, entry=yesterday, exit_=yesterday)
    pf.execute_buy("OPENNOW.NS", 1, 50.0, "intraday", reason="r")
    llm = FakeLLM("## review")
    l.generate_daily_review(llm)
    prompt = llm.calls[0]["messages"][0]["content"]
    closed_part, open_part = prompt.split("Positions Opened Today and Still Open")
    assert "SWING.NS" in closed_part and "OLD.NS" not in prompt
    assert "OPENNOW.NS" not in closed_part.split("Trades Closed Today")[1].split("##")[0]
    assert "OPENNOW.NS" in open_part.split("## Portfolio Summary")[0]


def test_window_uses_ist_date_not_utc_date(tmp_path):
    l, pf = _learner(tmp_path)
    t = _closed(pf)
    # 01:30 IST today == 20:00 UTC the previous calendar day
    ist_now = datetime.now(IST)
    early = ist_now.replace(hour=1, minute=30, second=0, microsecond=0)
    l._now_local = lambda: ist_now.replace(hour=16, minute=0)
    _set_times(pf, t.id, entry=early.astimezone(timezone.utc), exit_=early.astimezone(timezone.utc))
    closed, _ = l._todays_activity()
    assert [x.id for x in closed] == [t.id]


def test_crypto_uses_trailing_24h(tmp_path):
    l, pf = _learner(tmp_path, tz="UTC", is_24x7=True)
    recent, old = _closed(pf, ticker="NEW-USD"), _closed(pf, ticker="OLD-USD")
    now = datetime.now(timezone.utc)
    _set_times(pf, recent.id, exit_=now - timedelta(hours=10))
    _set_times(pf, old.id, exit_=now - timedelta(hours=40))
    closed, _ = l._todays_activity()
    assert [x.ticker for x in closed] == ["NEW-USD"]


# ── 8. all-time stats ──────────────────────────────────────────────────────

def test_stats_cover_all_closed_trades(tmp_path):
    l, pf = _learner(tmp_path)
    for _ in range(230):
        _closed(pf)
    assert l.get_performance_stats()["total_trades"] == 230
    assert "230 trades" in l._get_stats_by_category()
