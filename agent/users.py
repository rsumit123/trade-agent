"""User registry — sqlite-backed user metadata.

A user is created on first Google sign-in. Admins are determined by
the ADMIN_EMAILS env var (comma-separated email list).
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

# Sits next to sessions/ for easy backup
USERS_DB = Path(__file__).parent.parent / "users.db"


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
                last_login REAL
            )"""
        )


def upsert_user(email: str, name: str = "", picture: str = "") -> dict:
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
                "INSERT INTO users (email, name, picture, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, name, picture, now, now),
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
    return user


def is_admin(email: str) -> bool:
    return (email or "").lower().strip() in _admin_emails()
