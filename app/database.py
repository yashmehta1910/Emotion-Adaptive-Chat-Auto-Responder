"""
SQLite storage: message log + user settings.

One shared connection guarded by a re-entrant lock, because the web server and
the bot thread both read and write.
"""

import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import config

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

DEFAULT_SETTINGS: Dict[str, Any] = {
    "mode": "dry",               # dry | approve | auto
    "driver": config.DEFAULT_DRIVER if config.DEFAULT_DRIVER in ("mock", "whatsapp") else "mock",
    "target_chat": "",           # only act when the open chat's name contains this
    "style_notes": "",           # free text: how the user texts
    "poll_interval": 3.0,        # seconds between checks
    "reply_delay": 2.0,          # seconds to wait before an automatic send
    "history_turns": 6,          # earlier messages given to the AI as context
    "non_text_policy": "skip",   # skip | ack  (what to do with photos, voice notes...)
    "ack_text": "",              # reply text used when non_text_policy == "ack"
    "self_chat_test": False,     # treat your own messages as incoming (for "Message yourself")
    "reply_to_existing": False,  # also answer the message already there when the bot starts
}

_MESSAGE_FIELDS = {
    "chat", "msg_key", "incoming_text", "kind", "emotion", "reply_text",
    "status", "mode", "error", "latency_ms",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    chat          TEXT,
    msg_key       TEXT,
    incoming_text TEXT,
    kind          TEXT DEFAULT 'text',
    emotion       TEXT,
    reply_text    TEXT,
    status        TEXT NOT NULL,
    mode          TEXT,
    error         TEXT,
    latency_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_key ON messages(chat, msg_key);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init() -> None:
    """Create the database file and tables (safe to call more than once)."""
    global _conn
    with _lock:
        if _conn is not None:
            return
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        init()
    return _conn  # type: ignore[return-value]


# --- settings --------------------------------------------------------------

def get_settings() -> Dict[str, Any]:
    with _lock:
        rows = _db().execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        if row["key"] in DEFAULT_SETTINGS:
            try:
                settings[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
    return settings


def update_settings(partial: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        for key, value in partial.items():
            if key not in DEFAULT_SETTINGS:
                continue
            _db().execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
        _db().commit()
    return get_settings()


# --- messages --------------------------------------------------------------

def add_message(**fields: Any) -> int:
    unknown = set(fields) - _MESSAGE_FIELDS
    if unknown:
        raise ValueError(f"Unknown message fields: {unknown}")
    fields.setdefault("status", "skipped")
    now = time.time()
    cols = ["created_at", "updated_at"] + list(fields.keys())
    vals = [now, now] + list(fields.values())
    sql = f"INSERT INTO messages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})"
    with _lock:
        cur = _db().execute(sql, vals)
        _db().commit()
        return int(cur.lastrowid)


def update_message(message_id: int, **fields: Any) -> None:
    unknown = set(fields) - _MESSAGE_FIELDS
    if unknown:
        raise ValueError(f"Unknown message fields: {unknown}")
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
    vals = list(fields.values()) + [time.time(), message_id]
    with _lock:
        _db().execute(f"UPDATE messages SET {assignments} WHERE id = ?", vals)
        _db().commit()


def get_message(message_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        row = _db().execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def list_messages(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def list_by_status(status: str) -> List[Dict[str, Any]]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM messages WHERE status = ? ORDER BY id", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


def has_key(chat: str, msg_key: str) -> bool:
    with _lock:
        row = _db().execute(
            "SELECT 1 FROM messages WHERE chat = ? AND msg_key = ? LIMIT 1", (chat, msg_key)
        ).fetchone()
    return row is not None


def clear_messages() -> None:
    with _lock:
        _db().execute("DELETE FROM messages")
        _db().commit()


def stats() -> Dict[str, Any]:
    with _lock:
        by_status = {
            r["status"]: r["n"]
            for r in _db().execute("SELECT status, COUNT(*) AS n FROM messages GROUP BY status")
        }
        emotions = {
            r["emotion"]: r["n"]
            for r in _db().execute(
                "SELECT emotion, COUNT(*) AS n FROM messages WHERE emotion IS NOT NULL GROUP BY emotion"
            )
        }
        avg = _db().execute(
            "SELECT AVG(latency_ms) AS a FROM messages WHERE latency_ms IS NOT NULL"
        ).fetchone()["a"]
    return {
        "by_status": by_status,
        "emotions": emotions,
        "handled": sum(emotions.values()),
        "sent": by_status.get("sent", 0),
        "waiting": by_status.get("pending", 0) + by_status.get("approved", 0),
        "skipped": by_status.get("skipped", 0),
        "failed": by_status.get("failed", 0),
        "avg_latency_ms": int(avg) if avg is not None else None,
    }
