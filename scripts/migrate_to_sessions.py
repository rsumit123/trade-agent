#!/usr/bin/env python3
"""
Migrate existing single-session data to the new sessions/ directory structure.

Copies (not moves) existing files so the originals remain as a backup:
  data/trades.db       → sessions/nse_default/trades.db
  learnings/journal.md → sessions/nse_default/journal.md
  logs/agent.log       → sessions/nse_default/agent.log

Also writes sessions/nse_default/config.yaml with the current NSE defaults.
"""

import shutil
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "sessions"


def migrate():
    target = SESSIONS_DIR / "nse_default"

    if target.exists() and (target / "config.yaml").exists():
        print(f"✅ sessions/nse_default/ already exists — skipping migration.")
        print(f"   Location: {target}")
        return

    target.mkdir(parents=True, exist_ok=True)
    copied = []

    # Copy trades.db
    old_db = PROJECT_ROOT / "data" / "trades.db"
    if old_db.exists():
        shutil.copy2(old_db, target / "trades.db")
        copied.append(f"  {old_db} → {target / 'trades.db'}")

    # Copy journal.md
    old_journal = PROJECT_ROOT / "learnings" / "journal.md"
    if old_journal.exists():
        shutil.copy2(old_journal, target / "journal.md")
        copied.append(f"  {old_journal} → {target / 'journal.md'}")

    # Copy agent.log
    old_log = PROJECT_ROOT / "logs" / "agent.log"
    if old_log.exists():
        shutil.copy2(old_log, target / "agent.log")
        copied.append(f"  {old_log} → {target / 'agent.log'}")

    # Write config.yaml
    try:
        import yaml
    except ImportError:
        print("❌ pyyaml is required. Install it: pip install pyyaml")
        sys.exit(1)

    config = {
        "session_id": "nse_default",
        "display_name": "NSE Default",
        "market": "nse",
        "starting_capital": 10_00_000.0,
        "max_position_pct": 0.20,
        "max_open_positions": 5,
        "daily_loss_limit_pct": 0.02,
        "per_trade_loss_limit_pct": 0.01,
        "max_trade_amount": 2_00_000.0,
        "intraday_interval_min": 15,
        "llm_provider": "openrouter",
        "llm_model": "anthropic/claude-haiku-4-5",
        "api_key_env": "OPENROUTER_API_KEY",
        "created_at": datetime.now().isoformat(),
    }
    with open(target / "config.yaml", "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)
    copied.append(f"  Created {target / 'config.yaml'}")

    # Summary
    print("=" * 60)
    print("  Migration to sessions/ complete")
    print("=" * 60)
    if copied:
        print("\nFiles:")
        for line in copied:
            print(line)
    print(f"\n📁 Session directory: {target}")
    print("\n⚠️  Original files left in place as backup.")
    print("   Once verified, you can optionally remove data/, learnings/, logs/.")
    print(f"\n🧪 Test with: python run.py --session nse_default --status")


if __name__ == "__main__":
    migrate()
