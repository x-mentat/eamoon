#!/usr/bin/env python3
"""
Live migration script to copy data from SQLite to MySQL without downtime.

This script can be run while the system is actively writing to SQLite.
It will:
1. Create the MySQL database and table if they don't exist
2. Fetch all existing SQLite records
3. Copy them to MySQL in batches
4. Show progress as it goes

After migration, update your .env file to use:
    DB_TYPE=mysql
    MYSQL_HOST=your_host
    MYSQL_PORT=3306
    MYSQL_USER=your_user
    MYSQL_PASSWORD=your_password
    MYSQL_DATABASE=eamoon

Then restart your services.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# SQLite configuration
SQLITE_DB_PATH = os.getenv("DB_PATH", "inverter.db")

# MySQL configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "eamoon")

# Batch size for inserts
BATCH_SIZE = 100
RETRY_DELAY = 1  # seconds


def get_mysql_connection():
    """Create and return a MySQL connection."""
    import mysql.connector

    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        autocommit=False,
    )


def setup_mysql() -> None:
    """Create MySQL database and table if they don't exist."""
    print(f"Setting up MySQL database: {MYSQL_DATABASE}...")
    import mysql.connector

    # Create database
    root_conn = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        autocommit=True,
    )
    root_cursor = root_conn.cursor()
    root_cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE}")
    root_cursor.close()
    root_conn.close()
    print(f"  ✓ Database {MYSQL_DATABASE} ready")

    # Create table
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            created_at VARCHAR(255) NOT NULL,
            payload LONGTEXT,
            error TEXT,
            INDEX idx_created_at (created_at)
        )
    """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print("  ✓ Table 'readings' ready")


def get_sqlite_record_count() -> int:
    """Get total record count from SQLite."""
    if not Path(SQLITE_DB_PATH).exists():
        return 0

    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM readings")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return count


def get_sqlite_records_batch(offset: int, limit: int) -> list[tuple]:
    """Fetch a batch of records from SQLite."""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT created_at, payload, error FROM readings ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    records = cursor.fetchall()
    cursor.close()
    conn.close()
    return records


def insert_batch_to_mysql(records: list[tuple]) -> int:
    """
    Insert a batch of records to MySQL.
    Returns the number of successfully inserted records.
    """
    if not records:
        return 0

    conn = get_mysql_connection()
    cursor = conn.cursor()
    inserted = 0

    for created_at, payload_json, error in records:
        try:
            cursor.execute(
                "INSERT INTO readings (created_at, payload, error) VALUES (%s, %s, %s)",
                (created_at, payload_json, error),
            )
            inserted += 1
        except Exception as e:
            print(f"    Warning: Failed to insert record: {e}")
            # Try to continue with the next record
            continue

    conn.commit()
    cursor.close()
    conn.close()
    return inserted


def get_mysql_record_count() -> int:
    """Get total record count from MySQL."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM readings")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return count


def migrate(skip_existing: bool = False) -> None:
    """
    Main migration function.

    Args:
        skip_existing: If True, skip records that already exist in MySQL
    """
    print("\n" + "=" * 60)
    print("SQLite to MySQL Migration")
    print("=" * 60)

    # Validate SQLite
    sqlite_path = Path(SQLITE_DB_PATH)
    if not sqlite_path.exists():
        print(f"\n✗ SQLite database not found: {SQLITE_DB_PATH}")
        sys.exit(1)
    print(f"\n✓ Found SQLite database: {SQLITE_DB_PATH}")

    # Setup MySQL
    print("\nSetting up MySQL...")
    try:
        setup_mysql()
    except Exception as e:
        print(f"\n✗ Failed to setup MySQL: {e}")
        print("Check your MySQL connection settings in .env:")
        print(f"  MYSQL_HOST={MYSQL_HOST}")
        print(f"  MYSQL_PORT={MYSQL_PORT}")
        print(f"  MYSQL_USER={MYSQL_USER}")
        print(f"  MYSQL_DATABASE={MYSQL_DATABASE}")
        sys.exit(1)

    # Get counts
    sqlite_count = get_sqlite_record_count()
    mysql_count = get_mysql_record_count()

    print(f"\nRecord counts:")
    print(f"  SQLite: {sqlite_count} records")
    print(f"  MySQL:  {mysql_count} records")

    if sqlite_count == 0:
        print("\n✓ No records to migrate")
        return

    if mysql_count > 0 and not skip_existing:
        response = (
            input(
                f"\nMySQL already has {mysql_count} records. Continue? (y/n): "
            )
            .strip()
            .lower()
        )
        if response != "y":
            print("Migration cancelled.")
            return

    # Perform migration
    print(f"\nMigrating {sqlite_count} records in batches of {BATCH_SIZE}...")
    total_inserted = 0
    offset = 0

    try:
        while offset < sqlite_count:
            batch = get_sqlite_records_batch(offset, BATCH_SIZE)
            if not batch:
                break

            inserted = insert_batch_to_mysql(batch)
            total_inserted += inserted
            offset += len(batch)

            # Progress indicator
            percentage = int((offset / sqlite_count) * 100)
            bar_length = 40
            filled = int(bar_length * offset / sqlite_count)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"  [{bar}] {percentage}% ({offset}/{sqlite_count})")

            # Small delay to reduce load
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\n! Migration interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n✗ Migration failed: {e}")
        sys.exit(1)

    # Verify
    final_mysql_count = get_mysql_record_count()
    print(f"\n✓ Migration complete!")
    print(f"  SQLite records: {sqlite_count}")
    print(f"  MySQL records:  {final_mysql_count}")
    print(f"  Records inserted this run: {total_inserted}")

    if final_mysql_count == sqlite_count:
        print("\n✓ All records successfully migrated!")
        print("\nNext steps:")
        print("1. Update your .env file with:")
        print("   DB_TYPE=mysql")
        print(
            "   MYSQL_HOST="
            + (MYSQL_HOST if MYSQL_HOST != "localhost" else MYSQL_HOST)
        )
        print(f"   MYSQL_PORT={MYSQL_PORT}")
        print(f"   MYSQL_USER={MYSQL_USER}")
        print(f"   MYSQL_PASSWORD=<your_password>")
        print(f"   MYSQL_DATABASE={MYSQL_DATABASE}")
        print("2. Restart your services:")
        print("   systemctl restart easun-poller easun-web easun-bot")
    else:
        print(
            f"\n! Warning: Some records may not have been copied correctly"
        )
        print(
            f"  Expected {sqlite_count}, but found {final_mysql_count} in MySQL"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Migrate inverter readings from SQLite to MySQL"
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip the prompt if MySQL already has records",
    )
    args = parser.parse_args()

    migrate(skip_existing=args.skip_existing)


if __name__ == "__main__":
    main()
