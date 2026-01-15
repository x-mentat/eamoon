"""Storage for inverter readings with SQLite and MySQL support."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Database configuration from environment
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()  # "sqlite" or "mysql"
DB_TIMEOUT = 30.0

# MySQL connection parameters (only used if DB_TYPE=mysql)
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "eamoon")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "eamoon")

# Try to import MySQL connector
try:
    import pymysql
    pymysql.install_as_MySQLdb()
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False


def get_connection(db_path: str | Path = None):
    """Get database connection based on DB_TYPE."""
    if DB_TYPE == "mysql":
        if not MYSQL_AVAILABLE:
            raise RuntimeError("MySQL support requires pymysql: pip install pymysql")
        import pymysql
        return pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
    else:
        # SQLite
        path = Path(db_path) if db_path else Path("data/inverter.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(path, timeout=DB_TIMEOUT)


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_created_at ON readings(created_at DESC);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS readings (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    payload JSON,
    error TEXT,
    INDEX idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def init_db(db_path: str | Path = None) -> None:
    """Ensure the database and schema exist. Enable WAL mode for SQLite."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if DB_TYPE == "mysql":
            # Execute MySQL schema statements one by one
            for statement in SCHEMA_MYSQL.split(';'):
                statement = statement.strip()
                if statement:
                    cursor.execute(statement)
        else:
            # SQLite - enable WAL mode and create schema
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA_SQLITE)
        conn.commit()
    finally:
        conn.close()


def save_reading(
    db_path: str | Path,
    payload: Optional[Dict[str, Any]],
    error: Optional[str],
) -> None:
    """Persist a single reading (or error) to the database."""
    timestamp = datetime.datetime.utcnow()
    
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if DB_TYPE == "mysql":
            # MySQL uses JSON type and DATETIME
            payload_json = json.dumps(payload) if payload is not None else None
            cursor.execute(
                "INSERT INTO readings (created_at, payload, error) VALUES (%s, %s, %s)",
                (timestamp, payload_json, error),
            )
        else:
            # SQLite uses TEXT
            timestamp_str = timestamp.isoformat(timespec="seconds")
            payload_json = json.dumps(payload) if payload is not None else None
            cursor.execute(
                "INSERT INTO readings (created_at, payload, error) VALUES (?, ?, ?)",
                (timestamp_str, payload_json, error),
            )
        conn.commit()
    finally:
        conn.close()


def get_latest_reading(
    db_path: str | Path,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Fetch the most recent reading.
    Returns (payload_dict, error_text, created_at_iso).
    """
    if DB_TYPE == "sqlite":
        path = Path(db_path)
        if not path.exists():
            return None, "No database found; run modbus_service.py first.", None

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT payload, error, created_at FROM readings ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        
        if not row:
            return None, "No data recorded yet; run modbus_service.py.", None

        if DB_TYPE == "mysql":
            # MySQL returns dict cursor
            payload_json, error_text = row['payload'], row['error']
            created_at = row['created_at'].isoformat() if row['created_at'] else None
        else:
            # SQLite returns tuple
            payload_json, error_text, created_at = row
        
        payload = json.loads(payload_json) if payload_json else None
        return payload, error_text, created_at
    finally:
        conn.close()


def get_recent_readings(db_path: str | Path, limit: int = 50) -> list[Dict[str, Any]]:
    """
    Return the most recent readings as a list of dicts with keys: created_at, payload, error.
    """
    if DB_TYPE == "sqlite":
        path = Path(db_path)
        if not path.exists():
            return []

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if DB_TYPE == "mysql":
            cursor.execute(
                "SELECT created_at, payload, error FROM readings ORDER BY id DESC LIMIT %s",
                (limit,),
            )
        else:
            cursor.execute(
                "SELECT created_at, payload, error FROM readings ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        rows = cursor.fetchall()

        result = []
        for row in rows:
            if DB_TYPE == "mysql":
                created_at = row['created_at'].isoformat() if row['created_at'] else None
                payload_json, error = row['payload'], row['error']
            else:
                created_at, payload_json, error = row
            
            payload = json.loads(payload_json) if payload_json else None
            result.append({"created_at": created_at, "payload": payload, "error": error})
        return result
    finally:
        conn.close()


def get_readings_since(db_path: str | Path, days: float) -> list[Dict[str, Any]]:
    """
    Return readings from the past `days` days.
    """
    if DB_TYPE == "sqlite":
        path = Path(db_path)
        if not path.exists():
            return []

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if DB_TYPE == "mysql":
            cursor.execute(
                "SELECT created_at, payload, error FROM readings "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "ORDER BY id DESC",
                (days,),
            )
        else:
            cursor.execute(
                "SELECT created_at, payload, error FROM readings "
                "WHERE created_at >= datetime('now', ?) "
                "ORDER BY id DESC",
                (f"-{days} days",),
            )
        rows = cursor.fetchall()

        result = []
        for row in rows:
            if DB_TYPE == "mysql":
                created_at = row['created_at'].isoformat() if row['created_at'] else None
                payload_json, error = row['payload'], row['error']
            else:
                created_at, payload_json, error = row
            
            payload = json.loads(payload_json) if payload_json else None
            result.append({"created_at": created_at, "payload": payload, "error": error})
        return result
    finally:
        conn.close()
