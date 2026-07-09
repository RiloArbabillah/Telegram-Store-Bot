"""Migration: add DANA QRIS status/timestamp columns to transactions.

Run with: python migrations/004_dana_qris_fields.py
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
        add_column_if_missing(cursor, "transactions", "provider_status_code", "VARCHAR(50)")
        add_column_if_missing(cursor, "transactions", "provider_status_text", "VARCHAR(255)")
        add_column_if_missing(cursor, "transactions", "provider_paid_at", "DATETIME")
        add_column_if_missing(cursor, "transactions", "callback_received_at", "DATETIME")

        conn.commit()
        print("DANA QRIS migration completed successfully!")
        return True

    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
