#!/usr/bin/env python3
"""Add missing MySQL indexes for the readings table."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH if ENV_PATH.exists() else None)

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "eamoon")

REQUIRED_INDEXES = {
    "idx_created_at": "CREATE INDEX idx_created_at ON readings (created_at)",
    "idx_created_at_id": "CREATE INDEX idx_created_at_id ON readings (created_at, id)",
}


def get_connection():
    import mysql.connector

    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        autocommit=False,
    )


def get_existing_indexes(cursor) -> set[str]:
    cursor.execute(
        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'readings'",
        (MYSQL_DATABASE,),
    )
    return {row[0] for row in cursor.fetchall()}


def main() -> int:
    print("Checking MySQL indexes for readings table...")
    print(f"  MYSQL_HOST={MYSQL_HOST}")
    print(f"  MYSQL_PORT={MYSQL_PORT}")
    print(f"  MYSQL_USER={MYSQL_USER}")
    print(f"  MYSQL_DATABASE={MYSQL_DATABASE}")

    try:
        conn = get_connection()
        cursor = conn.cursor()
    except Exception as exc:
        print(f"✗ Failed to connect to MySQL: {exc}")
        return 1

    try:
        existing = get_existing_indexes(cursor)
        missing = [name for name in REQUIRED_INDEXES if name not in existing]

        if not missing:
            print("✓ No missing indexes found")
            return 0

        for name in missing:
            cursor.execute(REQUIRED_INDEXES[name])
            print(f"✓ Created missing index: {name}")

        conn.commit()
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"✗ Failed to add indexes: {exc}")
        return 1
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
