#!/usr/bin/env python3
"""
AI Trading Agent — Entry Point

Usage:
    python run.py                  # Run one decision cycle
    python run.py --loop           # Run continuously during market hours
    python run.py --loop --swing   # Swing trading mode (less frequent checks)
    python run.py --review         # Run daily review only
    python run.py --status         # Show portfolio status
"""

import argparse
import json
import sys
from agent import TradingAgent, AgentConfig


def main():
    parser = argparse.ArgumentParser(description="AI Trading Agent")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--swing", action="store_true", help="Swing trading mode")
    parser.add_argument("--review", action="store_true", help="Run daily review")
    parser.add_argument("--status", action="store_true", help="Show portfolio status")
    parser.add_argument("--capital", type=float, help="Starting capital (INR)")
    parser.add_argument("--provider", choices=["anthropic", "openai", "openrouter"], help="LLM provider")
    parser.add_argument("--force-intraday", action="store_true",
                        help="Force intraday trade_type for this cycle (useful for testing)")
    args = parser.parse_args()

    # Build config
    config = AgentConfig()
    if args.capital:
        config.starting_capital = args.capital
    if args.provider:
        config.llm_provider = args.provider

    # Initialize agent
    agent = TradingAgent(config)

    if args.status:
        summary = agent.portfolio.get_portfolio_summary()
        print("\n📊 Portfolio Status")
        print("=" * 50)
        print(f"  Cash:            ₹{summary['cash']:>12,.2f}")
        print(f"  Holdings Value:  ₹{summary['holdings_value']:>12,.2f}")
        print(f"  Total Value:     ₹{summary['total_value']:>12,.2f}")
        print(f"  Total Return:    ₹{summary['total_return']:>12,.2f} ({summary['total_return_pct']:+.2f}%)")
        print(f"  Today's P&L:     ₹{summary['today_pnl']:>12,.2f}")
        print(f"  Open Positions:  {summary['open_positions']}")
        if summary['holdings']:
            print("\n  Open Holdings:")
            for h in summary['holdings']:
                pnl_str = f"₹{h['unrealized_pnl']:+,.2f}"
                print(f"    {h['ticker']:15s} {h['qty']:>4}x @ ₹{h['entry_price']:.2f} → ₹{h['current_price']:.2f} ({pnl_str})")

        # Show performance stats
        stats = agent.learner.get_performance_stats()
        if stats.get("total_trades", 0) > 0:
            print(f"\n📈 Performance ({stats['total_trades']} trades)")
            print(f"  Win Rate: {stats['win_rate']}%")
            print(f"  Avg Win:  ₹{stats['avg_win']:,.2f}")
            print(f"  Avg Loss: ₹{stats['avg_loss']:,.2f}")
        print()
        return

    if args.review:
        agent.run_daily_review()
        return

    if args.loop:
        mode = "swing" if args.swing else "intraday"
        agent.run_loop(mode=mode)
    else:
        # Single cycle
        result = agent.run_once(force_intraday=args.force_intraday)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)}")


if __name__ == "__main__":
    main()
