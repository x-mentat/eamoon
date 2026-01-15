#!/usr/bin/env python3
"""Migrate data from SQLite to MySQL."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    import pymysql
except ImportError:
    print("ERROR: pymysql is required for migration")
    print("Install it with: pip install pymysql")
    sys.exit(1)


def migrate(sqlite_path: str, mysql_config: dict, batch_size: int = 1000):
    """Migrate all readings from SQLite to MySQL."""
    
    # Connect to SQLite
    print(f"Connecting to SQLite database: {sqlite_path}")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_cursor = sqlite_conn.cursor()
    
    # Get total count
    total = sqlite_cursor.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    print(f"Found {total} readings to migrate")
    
    if total == 0:
        print("No data to migrate")
        return
    
    # Connect to MySQL
    print(f"Connecting to MySQL database: {mysql_config['database']} at {mysql_config['host']}")
    mysql_conn = pymysql.connect(**mysql_config)
    mysql_cursor = mysql_conn.cursor()
    
    # Create table if not exists
    print("Creating MySQL schema...")
    mysql_cursor.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            created_at DATETIME NOT NULL,
            payload JSON,
            error TEXT,
            INDEX idx_created_at (created_at DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    mysql_conn.commit()
    
    # Migrate in batches
    print(f"Migrating data in batches of {batch_size}...")
    offset = 0
    migrated = 0
    
    while offset < total:
        # Fetch batch from SQLite
        sqlite_cursor.execute(
            "SELECT created_at, payload, error FROM readings ORDER BY id LIMIT ? OFFSET ?",
            (batch_size, offset)
        )
        rows = sqlite_cursor.fetchall()
        
        if not rows:
            break
        
        # Insert batch into MySQL
        for created_at_str, payload_json, error in rows:
            # Convert ISO string to datetime
            from datetime import datetime
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
            
            mysql_cursor.execute(
                "INSERT INTO readings (created_at, payload, error) VALUES (%s, %s, %s)",
                (created_at, payload_json, error)
            )
            migrated += 1
        
        mysql_conn.commit()
        print(f"Progress: {migrated}/{total} ({100*migrated/total:.1f}%)")
        offset += batch_size
    
    # Verify
    mysql_count = mysql_cursor.execute("SELECT COUNT(*) FROM readings")
    mysql_total = mysql_cursor.fetchone()[0]
    
    print(f"\nMigration complete!")
    print(f"SQLite records: {total}")
    print(f"MySQL records: {mysql_total}")
    
    if mysql_total >= total:
        print("✓ All records migrated successfully")
    else:
        print(f"⚠ Warning: Missing {total - mysql_total} records")
    
    sqlite_conn.close()
    mysql_conn.close()


def main():
    parser = argparse.ArgumentParser(description='Migrate inverter readings from SQLite to MySQL')
    parser.add_argument('--sqlite', default='data/inverter.db', help='SQLite database path')
    parser.add_argument('--mysql-host', default=os.getenv('MYSQL_HOST', 'localhost'), help='MySQL host')
    parser.add_argument('--mysql-port', type=int, default=int(os.getenv('MYSQL_PORT', '3306')), help='MySQL port')
    parser.add_argument('--mysql-user', default=os.getenv('MYSQL_USER', 'eamoon'), help='MySQL user')
    parser.add_argument('--mysql-password', default=os.getenv('MYSQL_PASSWORD', ''), help='MySQL password')
    parser.add_argument('--mysql-database', default=os.getenv('MYSQL_DATABASE', 'eamoon'), help='MySQL database')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for migration')
    
    args = parser.parse_args()
    
    # Check if SQLite file exists
    if not Path(args.sqlite).exists():
        print(f"ERROR: SQLite database not found: {args.sqlite}")
        sys.exit(1)
    
    mysql_config = {
        'host': args.mysql_host,
        'port': args.mysql_port,
        'user': args.mysql_user,
        'password': args.mysql_password,
        'database': args.mysql_database,
        'charset': 'utf8mb4',
    }
    
    try:
        migrate(args.sqlite, mysql_config, args.batch_size)
    except Exception as e:
        print(f"ERROR: Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
