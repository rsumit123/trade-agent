#!/usr/bin/env python3
"""Tag all existing sessions with the admin email so they remain visible
to the admin user after the auth migration.

Usage:
  python3 scripts/migrate_to_admin.py [--admin-email you@example.com]
  python3 scripts/migrate_to_admin.py --dry-run

If --admin-email is omitted, uses the first entry in ADMIN_EMAILS env var.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

SESSIONS_DIR = REPO_ROOT / "sessions"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-email", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    admin = args.admin_email
    if not admin:
        admins = os.environ.get("ADMIN_EMAILS", "")
        admin = admins.split(",")[0].strip() if admins else ""
    if not admin:
        print("ERROR: provide --admin-email or set ADMIN_EMAILS env var", file=sys.stderr)
        sys.exit(1)

    print(f"Admin email: {admin}")
    print(f"Sessions dir: {SESSIONS_DIR}")
    print(f"Dry run: {args.dry_run}\n")

    if not SESSIONS_DIR.exists():
        print("No sessions/ directory — nothing to migrate.")
        return

    tagged = 0
    skipped = 0
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        cfg = d / "config.yaml"
        if not cfg.exists():
            continue
        try:
            with open(cfg) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"  ! {d.name}: failed to read ({e})")
            continue

        existing = (data.get("user_email") or "").strip().lower()
        if existing:
            print(f"  - {d.name}: already owned by '{existing}', skipping")
            skipped += 1
            continue

        data["user_email"] = admin.lower()
        if args.dry_run:
            print(f"  [DRY] {d.name}: would tag → {admin.lower()}")
        else:
            with open(cfg, "w") as f:
                yaml.safe_dump(data, f, sort_keys=False)
            print(f"  ✓ {d.name}: tagged → {admin.lower()}")
        tagged += 1

    print(f"\nDone. Tagged: {tagged}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
