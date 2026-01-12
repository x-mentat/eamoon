"""Lightweight storage abstraction for SQLite and MySQL."""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import os

from timezone_utils import now_eet

# Database type detection from environment
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()
DB_PATH = os.getenv("DB_PATH", "inverter.db")

# MySQL configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "eamoon")

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS device_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_address TEXT NOT NULL,
    created_at TEXT NOT NULL,
    voltage REAL,
    current REAL,
    temperature REAL,
    cell_count INTEGER,
    cell_data TEXT,
    soc REAL,
    FOREIGN KEY (device_address) REFERENCES devices(address)
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT UNIQUE NOT NULL,
    name TEXT,
    device_type TEXT DEFAULT 'jk_bms',
    last_seen TEXT,
    created_at TEXT NOT NULL
);
"""

MYSQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at VARCHAR(255) NOT NULL,
    payload LONGTEXT,
    error TEXT,
    INDEX idx_created_at (created_at)
);
"""


def get_connection():
    """Get a database connection based on DB_TYPE."""
    if DB_TYPE == "mysql":
        import mysql.connector

        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            autocommit=False,
        )
    else:  # sqlite (default)
        return sqlite3.connect(DB_PATH)


def init_db(db_path: str | Path | None = None) -> None:
    """Ensure the database and schema exist."""
    if DB_TYPE == "mysql":
        import mysql.connector

        # First, create the database if it doesn't exist
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            autocommit=True,
        )
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE}")
        cursor.close()
        conn.close()

        # Now create the table
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(MYSQL_SCHEMA)
        conn.commit()
        cursor.close()
        conn.close()
    else:  # sqlite (default)
        # Use provided db_path or fall back to DB_PATH
        path_str = db_path or DB_PATH
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        # SQLITE_SCHEMA contains multiple statements; executescript runs them safely.
        conn.executescript(SQLITE_SCHEMA)
        conn.commit()
        conn.close()


def save_reading(
    db_path: str | Path | None,
    payload: Optional[Dict[str, Any]],
    error: Optional[str],
) -> None:
    """Persist a single reading (or error) to the database."""
    timestamp = now_eet().isoformat(timespec="seconds")
    payload_json = json.dumps(payload) if payload is not None else None

    if DB_TYPE == "mysql":
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO readings (created_at, payload, error) VALUES (%s, %s, %s)",
            (timestamp, payload_json, error),
        )
        conn.commit()
        cursor.close()
        conn.close()
    else:  # sqlite (default)
        path_str = db_path or DB_PATH
        path = Path(path_str)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO readings (created_at, payload, error) VALUES (?, ?, ?)",
            (timestamp, payload_json, error),
        )
        conn.commit()
        conn.close()


def get_latest_reading(
    db_path: str | Path | None = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Fetch the most recent reading.
    Returns (payload_dict, error_text, created_at_iso).
    """
    if DB_TYPE == "mysql":
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload, error, created_at FROM readings ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if not row:
                return None, "No data recorded yet; run modbus_service.py.", None

            payload_json, error_text, created_at = row
            payload = json.loads(payload_json) if payload_json else None
            return payload, error_text, created_at
        except Exception as e:
            return None, f"Database error: {str(e)}", None
    else:  # sqlite (default)
        path_str = db_path or DB_PATH
        path = Path(path_str)
        if not path.exists():
            return None, "No database found; run modbus_service.py first.", None

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT payload, error, created_at FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            return None, "No data recorded yet; run modbus_service.py.", None

        payload_json, error_text, created_at = row
        payload = json.loads(payload_json) if payload_json else None
        return payload, error_text, created_at


def get_recent_readings(db_path: str | Path | None = None, limit: int = 50) -> list[Dict[str, Any]]:
    """
    Return the most recent readings as a list of dicts with keys: created_at, payload, error.
    """
    if DB_TYPE == "mysql":
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at, payload, error FROM readings ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            result = []
            for created_at, payload_json, error in rows:
                payload = json.loads(payload_json) if payload_json else None
                result.append(
                    {"created_at": created_at, "payload": payload, "error": error}
                )
            return result
        except Exception:
            return []
    else:  # sqlite (default)
        path_str = db_path or DB_PATH
        path = Path(path_str)
        if not path.exists():
            return []

        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT created_at, payload, error FROM readings ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()

        result = []
        for created_at, payload_json, error in rows:
            payload = json.loads(payload_json) if payload_json else None
            result.append(
                {"created_at": created_at, "payload": payload, "error": error}
            )
        return result


def get_readings_since(db_path: str | Path | None = None, days: float = 1) -> list[Dict[str, Any]]:
    """
    Return readings from the past `days` days.
    """
    if DB_TYPE == "mysql":
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at, payload, error FROM readings "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "ORDER BY id DESC",
                (days,),
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            result = []
            for created_at, payload_json, error in rows:
                payload = json.loads(payload_json) if payload_json else None
                result.append(
                    {"created_at": created_at, "payload": payload, "error": error}
                )
            return result
        except Exception:
            return []
    else:  # sqlite (default)
        path_str = db_path or DB_PATH
        path = Path(path_str)
        if not path.exists():
            return []

        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT created_at, payload, error FROM readings "
            "WHERE created_at >= datetime('now', ?) "
            "ORDER BY id DESC",
            (f"-{days} days",),
        ).fetchall()
        conn.close()

        result = []
        for created_at, payload_json, error in rows:
            payload = json.loads(payload_json) if payload_json else None
            result.append(
                {"created_at": created_at, "payload": payload, "error": error}
            )
        return result
