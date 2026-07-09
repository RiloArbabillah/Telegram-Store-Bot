"""Migration: reset SQLite data and rebuild schema for whole-IDR integer money columns.

Run with: python migrations/005_currency_idr_cutover.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from database.db import init_db
from database.init_data import initialize_database


def get_db_path():
    """Extract SQLite database path from DATABASE_URL."""
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///"):
        return db_url.replace("sqlite:///", "")
    return None


def _drop_all_tables(db_path: str) -> None:
    """Drop every SQLite table so ORM metadata can recreate the schema cleanly."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall() if row[0] != "sqlite_sequence"]
        for table_name in table_names:
            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        conn.commit()
    finally:
        conn.close()


def migrate():
    """Reset SQLite data and recreate tables with integer IDR money columns."""
    db_path = get_db_path()
    if not db_path:
        print("This migration currently supports SQLite databases only.")
        return False

    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        print("Creating a fresh database with IDR schema...")
        initialize_database()
        return True

    print("Resetting SQLite database for IDR currency cutover...")
    _drop_all_tables(db_path)
    init_db()
    initialize_database()
    print("IDR currency cutover migration completed. Existing money data has been reset.")
    return True


if __name__ == "__main__":
    migrate()
