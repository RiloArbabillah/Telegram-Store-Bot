"""Migration: add manual QRIS fallback settings and proof fields.

Run with: python migrations/003_manual_qris_fallback.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings


def get_db_path():
    """Extract SQLite database path from DATABASE_URL."""
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///"):
        return db_url.replace("sqlite:///", "")
    return None


def add_column_if_missing(cursor, table_name, column_name, column_sql):
    """Add a SQLite column when it does not already exist."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {column[1] for column in cursor.fetchall()}
    if column_name in existing_columns:
        return False

    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    return True


def migrate():
    """Add QRIS settings fields and payment-proof transaction fields."""
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
        changed = False
        changed |= add_column_if_missing(cursor, "settings", "qris_instructions_text", "qris_instructions_text TEXT")
        changed |= add_column_if_missing(cursor, "settings", "qris_image_file_id", "qris_image_file_id VARCHAR(255)")
        changed |= add_column_if_missing(cursor, "transactions", "proof_file_id", "proof_file_id VARCHAR(255)")
        changed |= add_column_if_missing(cursor, "transactions", "proof_file_type", "proof_file_type VARCHAR(50)")
        changed |= add_column_if_missing(cursor, "transactions", "proof_submitted_at", "proof_submitted_at DATETIME")

        if not changed:
            print("manual QRIS fallback columns already present, skipping")
            return True

        conn.commit()
        print("Migration completed successfully!")
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
