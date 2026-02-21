"""
db.py — SQLite database layer for the Codebase AI Tool.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "codebases.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they don't already exist."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS codebases (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT    NOT NULL,
                path      TEXT    NOT NULL UNIQUE,
                added_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase_id  INTEGER NOT NULL REFERENCES codebases(id) ON DELETE CASCADE,
                rel_path     TEXT    NOT NULL,
                content      TEXT    NOT NULL,
                language     TEXT
            );

            CREATE TABLE IF NOT EXISTS contexts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase_id  INTEGER NOT NULL UNIQUE REFERENCES codebases(id) ON DELETE CASCADE,
                summary      TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase_id  INTEGER NOT NULL REFERENCES codebases(id) ON DELETE CASCADE,
                role         TEXT    NOT NULL,
                content      TEXT    NOT NULL,
                created_at   TEXT    NOT NULL
            );
        """)
    conn.close()


# ─── Codebases ───────────────────────────────────────────────────────────────

def add_codebase(name: str, path: str) -> int:
    """Insert a new codebase record and return its id."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO codebases (name, path, added_at) VALUES (?, ?, ?)",
            (name, path, now),
        )
        if cur.lastrowid:
            return cur.lastrowid
        # Already exists — return existing id
        row = conn.execute("SELECT id FROM codebases WHERE path = ?", (path,)).fetchone()
        return row["id"]


def get_all_codebases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM codebases ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_codebase_by_id(codebase_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM codebases WHERE id = ?", (codebase_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── Files ────────────────────────────────────────────────────────────────────

def clear_files(codebase_id: int):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM files WHERE codebase_id = ?", (codebase_id,))
    conn.close()


def add_file(codebase_id: int, rel_path: str, content: str, language: str = ""):
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO files (codebase_id, rel_path, content, language) VALUES (?, ?, ?, ?)",
            (codebase_id, rel_path, content, language),
        )
    conn.close()


def get_files(codebase_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM files WHERE codebase_id = ? ORDER BY rel_path",
        (codebase_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_file_content(codebase_id: int, rel_path: str, new_content: str):
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE files SET content = ? WHERE codebase_id = ? AND rel_path = ?",
            (new_content, codebase_id, rel_path),
        )
    conn.close()


# ─── Contexts ─────────────────────────────────────────────────────────────────

def save_context(codebase_id: int, summary: str):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO contexts (codebase_id, summary, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(codebase_id) DO UPDATE SET summary = excluded.summary, updated_at = excluded.updated_at
            """,
            (codebase_id, summary, now),
        )
    conn.close()


def get_context(codebase_id: int) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT summary FROM contexts WHERE codebase_id = ?", (codebase_id,)
    ).fetchone()
    conn.close()
    return row["summary"] if row else None


# ─── Chats ────────────────────────────────────────────────────────────────────

def add_chat_message(codebase_id: int, role: str, content: str):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            "INSERT INTO chats (codebase_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (codebase_id, role, content, now),
        )
    conn.close()


def get_chat_history(codebase_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM chats WHERE codebase_id = ? ORDER BY created_at",
        (codebase_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_chat_history(codebase_id: int):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM chats WHERE codebase_id = ?", (codebase_id,))
    conn.close()
