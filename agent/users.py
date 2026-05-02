"""User registry — sqlite-backed user metadata + free-tier runtime quota.

A user is created on first Google sign-in. Admins are determined by
the ADMIN_EMAILS env var (comma-separated email list).

Free-tier model: each non-admin user gets `FREE_RUNTIME_QUOTA_SECONDS`
of accumulated agent runtime. The clock only ticks while their agent is
actively running (pause = clock pauses).
"""

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

# Live under sessions/ so it's covered by the existing Docker volume mount
# and survives container rebuilds. (Previously /app/users.db lived in the
# container's writeable layer and got wiped on every `docker compose --build`.)
_SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
USERS_DB = _SESSIONS_DIR / "_users.db"

# One-time migration: if the new path doesn't exist but the legacy path does,
# move the legacy file in. Safe to retry — only runs while old path exists.
_LEGACY_USERS_DB = Path(__file__).parent.parent / "users.db"
if _LEGACY_USERS_DB.exists() and not USERS_DB.exists():
    try:
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(_LEGACY_USERS_DB), str(USERS_DB))
    except Exception:
        pass

# 24 hours of agent runtime per free user
FREE_RUNTIME_QUOTA_SECONDS = 24 * 60 * 60
# Paid tier (manually granted by admin): 5 days of agent runtime
PAID_RUNTIME_QUOTA_SECONDS = 5 * 24 * 60 * 60


def _admin_emails() -> set:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _conn():
    conn = sqlite3.connect(str(USERS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT,
                picture TEXT,
                created_at REAL,
                last_login REAL,
                runtime_quota_seconds INTEGER DEFAULT 86400,
                runtime_seconds_used INTEGER DEFAULT 0,
                trial_started_at REAL,
                tier TEXT DEFAULT 'free'
            )"""
        )
        # Migrations for existing dbs
        existing_cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "runtime_quota_seconds" not in existing_cols:
            c.execute("ALTER TABLE users ADD COLUMN runtime_quota_seconds INTEGER DEFAULT 86400")
        if "runtime_seconds_used" not in existing_cols:
            c.execute("ALTER TABLE users ADD COLUMN runtime_seconds_used INTEGER DEFAULT 0")
        if "trial_started_at" not in existing_cols:
            c.execute("ALTER TABLE users ADD COLUMN trial_started_at REAL")
        if "tier" not in existing_cols:
            c.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
        c.execute(
            """CREATE TABLE IF NOT EXISTS waitlist (
                email TEXT PRIMARY KEY,
                created_at REAL,
                source TEXT
            )"""
        )


def upsert_user(email: str, name: str = "", picture: str = "") -> dict:
    """Insert a new user or refresh login metadata for an existing one.

    On UPDATE we ONLY touch name / picture / last_login. Never tier, never
    quota, never used — those are managed exclusively via set_user_tier
    and set_runtime_quota. Re-login must never reset paid status.
    """
    init_db()
    email = email.lower().strip()
    now = time.time()
    with _conn() as c:
        existing = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            c.execute(
                "UPDATE users SET name = ?, picture = ?, last_login = ? WHERE email = ?",
                (name or existing["name"], picture or existing["picture"], now, email),
            )
        else:
            c.execute(
                "INSERT INTO users (email, name, picture, created_at, last_login, "
                "runtime_quota_seconds, runtime_seconds_used, tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'free')",
                (email, name, picture, now, now, FREE_RUNTIME_QUOTA_SECONDS, 0),
            )
    return get_user(email)


def get_user(email: str) -> Optional[dict]:
    init_db()
    email = email.lower().strip()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return None
    user = dict(row)
    user["is_admin"] = email in _admin_emails()
    # Admins effectively have admin tier regardless of stored value
    if user["is_admin"]:
        user["tier"] = "admin"
    elif not user.get("tier"):
        user["tier"] = "free"
    return user


def is_admin(email: str) -> bool:
    return (email or "").lower().strip() in _admin_emails()


def get_tier(email: str) -> str:
    """Returns 'admin', 'paid', or 'free'. Admin env override always wins."""
    if is_admin(email):
        return "admin"
    u = get_user(email)
    if not u:
        return "free"
    return u.get("tier") or "free"


def is_paid_or_admin(email: str) -> bool:
    """True if the user has access to paid features (paid tier or admin)."""
    return get_tier(email) in ("paid", "admin")


def set_user_tier(email: str, tier: str, *, grant_runtime: bool = True) -> dict:
    """Promote/demote a user. When promoting free->paid, optionally grants
    fresh runtime quota (and zeroes used) so the user starts clean."""
    if tier not in ("free", "paid"):
        raise ValueError(f"Invalid tier: {tier!r} (must be 'free' or 'paid')")
    init_db()
    email = (email or "").lower().strip()
    if is_admin(email):
        # Admin tier is env-driven; refuse DB writes that would mislead
        return get_user(email) or {}
    with _conn() as c:
        existing = c.execute("SELECT tier FROM users WHERE email = ?", (email,)).fetchone()
        if not existing:
            raise ValueError(f"User not found: {email}")
        if grant_runtime:
            new_quota = (
                PAID_RUNTIME_QUOTA_SECONDS if tier == "paid" else FREE_RUNTIME_QUOTA_SECONDS
            )
            c.execute(
                "UPDATE users SET tier = ?, runtime_quota_seconds = ?, runtime_seconds_used = 0 "
                "WHERE email = ?",
                (tier, new_quota, email),
            )
        else:
            c.execute("UPDATE users SET tier = ? WHERE email = ?", (tier, email))
    return get_user(email) or {}


def set_runtime_quota(email: str, quota_seconds: int, used_seconds: Optional[int] = None) -> dict:
    """Admin override: set raw quota and (optionally) reset used."""
    init_db()
    email = (email or "").lower().strip()
    with _conn() as c:
        if used_seconds is not None:
            c.execute(
                "UPDATE users SET runtime_quota_seconds = ?, runtime_seconds_used = ? "
                "WHERE email = ?",
                (int(quota_seconds), int(used_seconds), email),
            )
        else:
            c.execute(
                "UPDATE users SET runtime_quota_seconds = ? WHERE email = ?",
                (int(quota_seconds), email),
            )
    return get_user(email) or {}


def list_all_users() -> list:
    """Admin view — list all users with metadata."""
    init_db()
    admins = _admin_emails()
    out = []
    with _conn() as c:
        rows = c.execute(
            "SELECT email, name, picture, created_at, last_login, "
            "runtime_quota_seconds, runtime_seconds_used, trial_started_at, tier "
            "FROM users ORDER BY last_login DESC"
        ).fetchall()
    for r in rows:
        d = dict(r)
        d["is_admin"] = d["email"] in admins
        if d["is_admin"]:
            d["tier"] = "admin"
        elif not d.get("tier"):
            d["tier"] = "free"
        out.append(d)
    return out


def list_waitlist() -> list:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT email, created_at, source FROM waitlist ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Runtime quota helpers ───────────────────────────────────────


def get_runtime_remaining(email: str) -> int:
    """Seconds of runtime remaining for this user. Admins → very large."""
    if is_admin(email):
        return 10**9
    u = get_user(email)
    if not u:
        return 0
    tier = u.get("tier") or "free"
    default_quota = (
        PAID_RUNTIME_QUOTA_SECONDS if tier == "paid" else FREE_RUNTIME_QUOTA_SECONDS
    )
    quota = int(u.get("runtime_quota_seconds") or default_quota)
    used = int(u.get("runtime_seconds_used") or 0)
    return max(0, quota - used)


def add_runtime(email: str, seconds: int) -> int:
    """Add `seconds` to user's used runtime. Returns new used total. Admins: no-op."""
    if seconds <= 0 or is_admin(email):
        return 0
    init_db()
    email = email.lower().strip()
    with _conn() as c:
        c.execute(
            "UPDATE users SET runtime_seconds_used = COALESCE(runtime_seconds_used, 0) + ? "
            "WHERE email = ?",
            (int(seconds), email),
        )
        row = c.execute(
            "SELECT runtime_seconds_used FROM users WHERE email = ?", (email,)
        ).fetchone()
    return int(row[0]) if row else 0


def mark_trial_started(email: str) -> None:
    """Stamp the first time the user starts an agent (for analytics only)."""
    if is_admin(email):
        return
    init_db()
    email = email.lower().strip()
    now = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT trial_started_at FROM users WHERE email = ?", (email,)
        ).fetchone()
        if row and not row[0]:
            c.execute(
                "UPDATE users SET trial_started_at = ? WHERE email = ?",
                (now, email),
            )


def add_to_waitlist(email: str, source: str = "") -> bool:
    """Add an email to the upgrade waitlist. Returns True on insert, False if dup."""
    init_db()
    email = (email or "").lower().strip()
    if not email or "@" not in email:
        return False
    with _conn() as c:
        existing = c.execute("SELECT 1 FROM waitlist WHERE email = ?", (email,)).fetchone()
        if existing:
            return False
        c.execute(
            "INSERT INTO waitlist (email, created_at, source) VALUES (?, ?, ?)",
            (email, time.time(), source),
        )
    return True
