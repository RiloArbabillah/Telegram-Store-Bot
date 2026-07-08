"""Migration: normalize transaction provider fields and add QRIS-ready columns.

Run with: python migrations/002_normalize_transactions.py
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


def normalize_transaction_row(payment_method, crypto_address):
    """Backfill normalized provider fields from legacy transaction data."""
    provider_name = None
    external_reference = None
    checkout_url = None

    if payment_method == "CRYPTO_WALLET":
        provider_name = "cryptobot"
        if crypto_address and "|" in crypto_address:
            external_reference, checkout_url = crypto_address.split("|", 1)
        elif crypto_address and crypto_address.startswith("http"):
            checkout_url = crypto_address
        elif crypto_address:
            external_reference = crypto_address

    elif payment_method == "CARD":
        provider_name = "telegram_payments"
        if crypto_address and crypto_address.startswith("tg_charge:"):
            external_reference = crypto_address.split(":", 1)[1]
        elif crypto_address:
            external_reference = crypto_address

    return provider_name, external_reference, checkout_url


def migrate():
    """Recreate the transactions table with normalized provider fields."""
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
        cursor.execute("PRAGMA table_info(transactions)")
        columns = {column[1] for column in cursor.fetchall()}
        if "provider_name" in columns:
            print("transactions table already normalized, skipping")
            return True

        print("Starting migration: normalize transactions table...")

        cursor.execute(
            """
            CREATE TABLE transactions_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount FLOAT NOT NULL,
                payment_method VARCHAR(13) NOT NULL,
                provider_name VARCHAR(100),
                external_reference VARCHAR(255),
                checkout_url VARCHAR(500),
                qr_payload TEXT,
                provider_metadata TEXT,
                crypto_address VARCHAR(500),
                status VARCHAR(9),
                created_at DATETIME,
                expires_at DATETIME,
                completed_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        cursor.execute(
            """
            SELECT id, user_id, amount, payment_method, crypto_address, status,
                   created_at, expires_at, completed_at
            FROM transactions
            """
        )
        rows = cursor.fetchall()

        for row in rows:
            txn_id, user_id, amount, payment_method, crypto_address, status, created_at, expires_at, completed_at = row
            provider_name, external_reference, checkout_url = normalize_transaction_row(payment_method, crypto_address)
            cursor.execute(
                """
                INSERT INTO transactions_new (
                    id, user_id, amount, payment_method, provider_name,
                    external_reference, checkout_url, qr_payload, provider_metadata,
                    crypto_address, status, created_at, expires_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    txn_id,
                    user_id,
                    amount,
                    payment_method,
                    provider_name,
                    external_reference,
                    checkout_url,
                    None,
                    None,
                    crypto_address,
                    status,
                    created_at,
                    expires_at,
                    completed_at,
                ),
            )

        cursor.execute("DROP TABLE transactions")
        cursor.execute("ALTER TABLE transactions_new RENAME TO transactions")
        cursor.execute("CREATE INDEX ix_transactions_user_id ON transactions (user_id)")
        cursor.execute("CREATE INDEX ix_transactions_status ON transactions (status)")

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
