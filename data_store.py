"""Lightweight SQLite storage for inverter readings."""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

Schema = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload TEXT,
    error TEXT
);
"""


def init_db(db_path: str | Path) -> None:
    """Ensure the database and schema exist."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(Schema)
        conn.commit()


def save_reading(db_path: str | Path, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
    """Persist a single reading (or error) to the database."""
    path = Path(db_path)
    timestamp = datetime.datetime.utcnow().isoformat(timespec="seconds")
    payload_json = json.dumps(payload) if payload is not None else None
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO readings (created_at, payload, error) VALUES (?, ?, ?)",
            (timestamp, payload_json, error),
        )
        conn.commit()


def get_latest_reading(db_path: str | Path) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Fetch the most recent reading.
    Returns (payload_dict, error_text, created_at_iso).
    """
    path = Path(db_path)
    if not path.exists():
        return None, "No database found; run modbus_service.py first.", None

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT payload, error, created_at FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if not row:
        return None, "No data recorded yet; run modbus_service.py.", None

    payload_json, error_text, created_at = row
    payload = json.loads(payload_json) if payload_json else None
    return payload, error_text, created_at


def get_recent_readings(db_path: str | Path, limit: int = 50) -> list[Dict[str, Any]]:
    """
    Return the most recent readings as a list of dicts with keys: created_at, payload, error.
    """
    path = Path(db_path)
    if not path.exists():
        return []

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT created_at, payload, error FROM readings ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    result = []
    for created_at, payload_json, error in rows:
        payload = json.loads(payload_json) if payload_json else None
        result.append({"created_at": created_at, "payload": payload, "error": error})
    return result


def get_readings_since(db_path: str | Path, days: float) -> list[Dict[str, Any]]:
    """
    Return readings from the past `days` days.
    """
    path = Path(db_path)
    if not path.exists():
        return []

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT created_at, payload, error FROM readings "
            "WHERE created_at >= datetime('now', ?)"
            "ORDER BY id DESC",
            (f"-{days} days",),
        ).fetchall()

    result = []
    for created_at, payload_json, error in rows:
        payload = json.loads(payload_json) if payload_json else None
        result.append({"created_at": created_at, "payload": payload, "error": error})
    return result
