import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings

_db_lock = threading.Lock()


def get_db_path(database_url: Optional[str] = None) -> str:
    url = database_url or settings.DATABASE_URL
    if url.startswith("sqlite:////"):
        # e.g. sqlite:////data/app.db -> /data/app.db (unix absolute)
        path = "/" + url[len("sqlite:////"):]
    elif url.startswith("sqlite:///"):
        # e.g. sqlite:///./data/app.db or sqlite:///C:/path
        path = url[len("sqlite:///"): ]
    else:
        path = url
    # Ensure parent directory exists
    abs_dir = os.path.dirname(os.path.abspath(path))
    if abs_dir and not os.path.exists(abs_dir):
        os.makedirs(abs_dir, exist_ok=True)
    return path


@contextmanager
def get_connection(db_path: Optional[str] = None):
    path = get_db_path(db_path)
    with _db_lock:
        conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    with get_connection(db_path) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id);
            """)


def check_db_health(db_path: Optional[str] = None) -> bool:
    try:
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1;")
            res = cur.fetchone()
            return res is not None and res[0] == 1
    except Exception:
        return False


def get_session_system_prompt(session_id: str, db_path: Optional[str] = None) -> Optional[str]:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT system_prompt FROM sessions WHERE session_id = ?;", (session_id,))
        row = cur.fetchone()
        return row["system_prompt"] if row else None


def get_session_history(session_id: str, db_path: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        # Verify if session exists
        cur.execute("SELECT session_id FROM sessions WHERE session_id = ?;", (session_id,))
        session_row = cur.fetchone()
        if not session_row:
            # Also check if messages exist for this session
            cur.execute("SELECT id FROM messages WHERE session_id = ? LIMIT 1;", (session_id,))
            if not cur.fetchone():
                return None

        cur.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC;",
            (session_id,)
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            meta = None
            if r["metadata"]:
                try:
                    meta = json.loads(r["metadata"])
                except Exception:
                    meta = None
            item = {
                "role": r["role"],
                "content": r["content"]
            }
            if r["role"] == "assistant" and meta is not None:
                item["metadata"] = meta
            result.append(item)
        return result


def save_chat_turn(
    session_id: str,
    user_message: str,
    assistant_reply: str,
    assistant_metadata: Dict[str, Any],
    system_prompt: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        with conn:
            # Ensure session is registered
            cur = conn.cursor()
            cur.execute("SELECT session_id, system_prompt FROM sessions WHERE session_id = ?;", (session_id,))
            existing = cur.fetchone()
            if not existing:
                prompt_to_save = system_prompt or settings.DEFAULT_SYSTEM_PROMPT
                cur.execute(
                    "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?);",
                    (session_id, prompt_to_save, now_iso)
                )
            elif system_prompt is not None and system_prompt != existing["system_prompt"]:
                # Update system prompt if explicitly overridden
                cur.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE session_id = ?;",
                    (system_prompt, session_id)
                )

            # Insert user message
            cur.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?);",
                (session_id, "user", user_message, None, now_iso)
            )

            # Insert assistant message
            metadata_str = json.dumps(assistant_metadata)
            cur.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?);",
                (session_id, "assistant", assistant_reply, metadata_str, now_iso)
            )
