#!/usr/bin/env python3
"""
AI Trading Agent — Entry Point

Usage:
    python run.py                          # Run one decision cycle (legacy NSE)
    python run.py --loop                   # Run continuously during market hours
    python run.py --loop --swing           # Swing trading mode
    python run.py --review                 # Run daily review only
    python run.py --status                 # Show portfolio status

    # Multi-session support:
    python run.py --list-sessions          # List all configured sessions
    python run.py --create-session         # Interactive wizard to create a session
    python run.py --session crypto_btc --loop   # Run a specific session
    python run.py --session nse_default --status
"""

import argparse
import json
import os
import sys
import atexit
from pathlib import Path
from datetime import datetime

# ── PID Lock — prevent multiple instances of the SAME session ──────


def _acquire_lock(lock_path: Path):
    """Write PID to lock file. Exit if another instance is already running."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            os.kill(existing_pid, 0)
            print(f"❌ Another agent instance is already running (PID {existing_pid}).")
            print(f"   To stop it: kill {existing_pid}")
            print(f"   Lock file: {lock_path}")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass  # Stale lock — safe to overwrite
    lock_path.write_text(str(os.getpid()))
    atexit.register(_release_lock, lock_path)


def _release_lock(lock_path: Path):
    """Remove lock file on clean exit."""
    try:
        if lock_path.exists() and lock_path.read_text().strip() == str(os.getpid()):
            lock_path.unlink()
    except Exception:
        pass


# Legacy lock path for non-session mode
_LEGACY_LOCK = Path(__file__).parent / "data" / "agent.lock"


def main():
    parser = argparse.ArgumentParser(description="AI Trading Agent")

    # Session args
    parser.add_argument("--session", type=str, help="Session ID to run (e.g., 'nse_default', 'crypto_btc')")
    parser.add_argument("--create-session", action="store_true", help="Create a new trading session")
    parser.add_argument("--list-sessions", action="store_true", help="List all configured sessions")

    # Existing args
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--swing", action="store_true", help="Swing trading mode")
    parser.add_argument("--review", action="store_true", help="Run daily review")
    parser.add_argument("--status", action="store_true", help="Show portfolio status")
    parser.add_argument("--capital", type=float, help="Starting capital (override)")
    parser.add_argument("--provider", choices=["anthropic", "openai", "openrouter"], help="LLM provider")
    parser.add_argument("--force-intraday", action="store_true",
                        help="Force intraday trade_type for this cycle")
    args = parser.parse_args()

    # ── List sessions ──────────────────────────────────────────
    if args.list_sessions:
        from agent.session import list_sessions
        sessions = list_sessions()
        if not sessions:
            print("\n  No sessions found. Create one with: python run.py --create-session\n")
        else:
            print(f"\n  {'ID':20s}  {'Market':8s}  {'Capital':>12s}  Name")
            print("  " + "-" * 65)
            for s in sessions:
                sym = s.get("currency_symbol", "?")
                cap = s.get("starting_capital", 0)
                print(f"  {s['session_id']:20s}  {s['market']:8s}  {sym}{cap:>10,.0f}  {s['display_name']}")
            print()
        return

    # ── Create session wizard ──────────────────────────────────
    if args.create_session:
        _create_session_wizard()
        return

    # ── Initialize agent ───────────────────────────────────────
    if args.session:
        # Session-based mode
        from agent.session import load_session
        from agent import TradingAgent
        session = load_session(args.session)
        lock_path = session.lock_path

        if args.loop:
            _acquire_lock(lock_path)

        agent = TradingAgent(session=session)
        sym = agent.config.currency_symbol
    else:
        # Legacy mode (backward compat — uses old data/ paths)
        from agent import TradingAgent, AgentConfig
        config = AgentConfig()
        if args.capital:
            config.starting_capital = args.capital
        if args.provider:
            config.llm_provider = args.provider

        lock_path = _LEGACY_LOCK
        if args.loop:
            _acquire_lock(lock_path)

        agent = TradingAgent(config)
        sym = config.currency_symbol

    # ── Status ─────────────────────────────────────────────────
    if args.status:
        summary = agent.portfolio.get_portfolio_summary()
        print(f"\n📊 Portfolio Status" + (f" [{args.session}]" if args.session else ""))
        print("=" * 50)
        print(f"  Cash:            {sym}{summary['cash']:>12,.2f}")
        print(f"  Holdings Value:  {sym}{summary['holdings_value']:>12,.2f}")
        print(f"  Total Value:     {sym}{summary['total_value']:>12,.2f}")
        print(f"  Total Return:    {sym}{summary['total_return']:>12,.2f} ({summary['total_return_pct']:+.2f}%)")
        print(f"  Today's P&L:     {sym}{summary['today_pnl']:>12,.2f}")
        print(f"  Open Positions:  {summary['open_positions']}")
        if summary['holdings']:
            print("\n  Open Holdings:")
            for h in summary['holdings']:
                pnl_str = f"{sym}{h['unrealized_pnl']:+,.2f}"
                print(f"    {h['ticker']:15s} {h['qty']:>4}x @ {sym}{h['entry_price']:.2f} → {sym}{h['current_price']:.2f} ({pnl_str})")

        stats = agent.learner.get_performance_stats()
        if stats.get("total_trades", 0) > 0:
            print(f"\n📈 Performance ({stats['total_trades']} trades)")
            print(f"  Win Rate: {stats['win_rate']}%")
            print(f"  Avg Win:  {sym}{stats['avg_win']:,.2f}")
            print(f"  Avg Loss: {sym}{stats['avg_loss']:,.2f}")
        print()
        return

    # ── Review ─────────────────────────────────────────────────
    if args.review:
        agent.run_daily_review()
        return

    # ── Main loop or single cycle ──────────────────────────────
    if args.loop:
        mode = "swing" if args.swing else "intraday"
        agent.run_loop(mode=mode)
    else:
        result = agent.run_once(force_intraday=args.force_intraday)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)}")


def _create_session_wizard():
    """Interactive wizard to create a new trading session."""
    from agent.session import SessionConfig, save_session
    from agent.market_presets import MARKET_PRESETS, get_preset

    print("\n" + "=" * 50)
    print("  🆕  Create New Trading Session")
    print("=" * 50)

    # Session ID
    session_id = input("\n  Session ID (e.g., crypto_btc, nse_aggressive): ").strip()
    if not session_id:
        print("  ❌ Session ID is required.")
        return

    # Display name
    display_name = input(f"  Display name [{session_id}]: ").strip() or session_id

    # Market selection
    markets = list(MARKET_PRESETS.keys())
    print(f"\n  Available markets: {', '.join(markets)}")
    market = input(f"  Market [{markets[0]}]: ").strip() or markets[0]
    if market not in MARKET_PRESETS:
        print(f"  ❌ Unknown market: {market}")
        return
    preset = get_preset(market)

    # Starting capital
    cap_str = input(f"  Starting capital ({preset.currency}) [{preset.default_starting_capital:,.0f}]: ").strip()
    capital = float(cap_str) if cap_str else None

    # LLM provider
    provider = input("  LLM provider [openrouter]: ").strip() or "openrouter"

    # Model
    default_model = "anthropic/claude-haiku-4-5"
    model = input(f"  Model [{default_model}]: ").strip() or default_model

    # API key env var
    api_key_env = input("  API key env var [OPENROUTER_API_KEY]: ").strip() or "OPENROUTER_API_KEY"

    # Personality
    print("\n  Trading personality (custom instructions for the LLM).")
    print("  Examples: 'Be aggressive, target 2% daily' or 'Conservative, only blue chips'")
    personality = input("  Personality (or press Enter for default): ").strip()

    # Watchlist override
    print(f"\n  Default watchlist: {len(preset.default_watchlist)} {market} assets")
    custom_wl = input("  Custom watchlist (comma-separated tickers, or Enter for default): ").strip()
    watchlist = [t.strip() for t in custom_wl.split(",") if t.strip()] if custom_wl else None

    # Create and save
    sc = SessionConfig(
        session_id=session_id,
        display_name=display_name,
        market=market,
        starting_capital=capital,
        llm_provider=provider,
        llm_model=model,
        api_key_env=api_key_env,
        personality=personality,
        watchlist=watchlist,
        created_at=datetime.now().isoformat(),
    )
    sc.resolve_defaults()
    save_session(sc)

    print(f"\n  ✅ Session '{session_id}' created!")
    print(f"  📁 {sc.session_dir}")
    print(f"  🧪 Test: python run.py --session {session_id} --status")
    print(f"  🚀 Run:  python run.py --session {session_id} --loop\n")


if __name__ == "__main__":
    main()
