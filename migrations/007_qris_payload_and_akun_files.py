"""Migration: add decoded QRIS payload and AKUN supporting files.

Run with: python migrations/007_qris_payload_and_akun_files.py
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


def add_column_if_missing(cursor, table_name, column_name, column_sql):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {column[1] for column in cursor.fetchall()}
    if column_name in existing_columns:
        return False

    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    return True


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
        changed = False
        changed |= add_column_if_missing(cursor, "settings", "qris_static_payload", "qris_static_payload TEXT")
        changed |= add_column_if_missing(cursor, "products", "supporting_files", "supporting_files TEXT")

        if not changed:
            print("QRIS payload and AKUN supporting file columns already present, skipping")
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
