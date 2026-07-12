"""Migration: link transactions to orders and remove user wallet balance.

Run with: python migrations/009_direct_checkout_remove_wallet.py
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


def table_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [column[1] for column in cursor.fetchall()]


def add_column_if_missing(cursor, table_name, column_name, column_sql):
    if column_name in table_columns(cursor, table_name):
        return False
    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    return True


def drop_user_wallet_balance(cursor):
    columns = table_columns(cursor, "users")
    if "wallet_balance" not in columns:
        return False

    cursor.execute("PRAGMA foreign_keys=off")
    cursor.execute(
        """
        CREATE TABLE users_new (
            id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            username VARCHAR(255),
            is_banned BOOLEAN,
            created_at DATETIME,
            PRIMARY KEY (id),
            UNIQUE (telegram_id)
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO users_new (id, telegram_id, username, is_banned, created_at)
        SELECT id, telegram_id, username, is_banned, created_at FROM users
        """
    )
    cursor.execute("DROP TABLE users")
    cursor.execute("ALTER TABLE users_new RENAME TO users")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id)")
    cursor.execute("PRAGMA foreign_keys=on")
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
        changed |= add_column_if_missing(cursor, "transactions", "order_id", "order_id INTEGER")
        changed |= drop_user_wallet_balance(cursor)

        conn.commit()
        print("Migration completed successfully!" if changed else "Migration already applied, skipping")
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
