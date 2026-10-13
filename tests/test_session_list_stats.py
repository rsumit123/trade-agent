import sqlite3

import agent.session
import dashboard.app as app


def _session_with_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.session, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(app, "_is_agent_running", lambda sid: {"running": True, "pid": 42})
    (tmp_path / "s1").mkdir()
    con = sqlite3.connect(tmp_path / "s1" / "trades.db")
    con.execute("create table trades (status text, pnl real, entry_time text)")
    con.execute("create table daily_snapshots (date text, total_value real, daily_pnl real)")
    con.executemany("insert into trades values (?,?,?)", [
        ("closed", 912.0, "2026-10-13T04:06"), ("closed", -408.0, "2026-10-12T04:06"),
        ("open", None, "2026-10-13T05:00"),
    ])
    con.commit()
    con.close()
    return {"session_id": "s1", "starting_capital": 500_000}


def test_list_stats_include_portfolio(tmp_path, monkeypatch):
    s = _session_with_trades(tmp_path, monkeypatch)
    app._attach_list_stats(s)
    assert s["total_trades"] == 2 and s["win_rate"] == 50.0
    assert s["portfolio"]["total_return"] == 504.0
    assert s["portfolio"]["open_positions"] == 1


def test_admin_list_has_same_stats_as_user_list(tmp_path, monkeypatch):
    # The admin list used to omit "portfolio", so every card showed "--" for return.
    s = _session_with_trades(tmp_path, monkeypatch)
    monkeypatch.setattr(agent.session, "list_sessions", lambda: [dict(s)])
    [admin] = app.get_admin_sessions({})
    assert admin["portfolio"]["total_return"] == 504.0
    assert admin["is_running"] is True


def test_list_card_matches_dashboard_win_rate_and_live_sparkline(tmp_path, monkeypatch):
    # Win rate excludes breakevens (like the dashboard) and the sparkline drops
    # pre-Go-Live (backtest) snapshots.
    s = _session_with_trades(tmp_path, monkeypatch)
    con = sqlite3.connect(tmp_path / "s1" / "trades.db")
    con.execute("insert into trades values ('closed', 0.0, '2026-10-13T06:00')")
    con.executemany("insert into daily_snapshots values (?,?,?)", [
        ("2026-04-01", 480_000.0, -5.0), ("2026-10-12", 500_100.0, 100.0),
    ])
    con.commit()
    con.close()
    s["live_started_at"] = "2026-10-01T00:00:00"
    app._attach_list_stats(s)
    assert s["total_trades"] == 3 and s["win_rate"] == 50.0  # 1 win / (1 win + 1 loss)
    assert [d["date"] for d in s["daily"]] == ["2026-10-12"]
