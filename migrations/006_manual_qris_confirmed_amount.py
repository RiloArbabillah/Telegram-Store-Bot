"""Migration: add confirmed_amount to transactions for manual QRIS overrides.

Run with: python migrations/006_manual_qris_confirmed_amount.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings


def get_db_path():
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///"):
        return db_url.replace("sqlite:///", "")
    return None


def add_column_if_missing(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {column_info[1] for column_info in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate():
    db_path = get_db_path()
    if not db_path:
        print("This migration currently supports SQLite databases only.")
        return False

    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        print("Run the bot first to create the database, then run this migration.")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        add_column_if_missing(cursor, "transactions", "confirmed_amount", "INTEGER")
        conn.commit()
        print("Manual QRIS confirmed amount migration completed successfully!")
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
