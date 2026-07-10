import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    AdminAuditLog,
    AdminLoginToken,
    BroadcastDelivery,
    BroadcastJob,
    Base,
    StockAdjustment,
)
from services.admin_auth import build_login_url, create_login_token, consume_login_token


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_creates_hashed_one_time_token(self):
        with self.Session.begin() as session:
            raw_token = create_login_token(session, 123, now=datetime(2026, 7, 10, 12, 0, 0))
            record = session.query(AdminLoginToken).one()

            self.assertNotEqual(record.token_hash, raw_token)
            self.assertEqual(record.admin_telegram_id, 123)
            self.assertEqual(record.expires_at, datetime(2026, 7, 10, 12, 5, 0))

    def test_consumes_valid_token_only_once(self):
        with self.Session.begin() as session:
            raw_token = create_login_token(session, 123, now=datetime(2026, 7, 10, 12, 0, 0))

        with self.Session.begin() as session:
            admin_id = consume_login_token(
                session,
                raw_token,
                expected_admin_id=123,
                now=datetime(2026, 7, 10, 12, 1, 0),
            )
        self.assertEqual(admin_id, 123)

        with self.Session.begin() as session:
            replay = consume_login_token(
                session,
                raw_token,
                expected_admin_id=123,
                now=datetime(2026, 7, 10, 12, 2, 0),
            )
        self.assertIsNone(replay)

    def test_rejects_expired_or_wrong_admin_token(self):
        with self.Session.begin() as session:
            expired = create_login_token(session, 123, now=datetime(2026, 7, 10, 12, 0, 0))
            wrong_admin = create_login_token(session, 456, now=datetime(2026, 7, 10, 12, 0, 0))

        with self.Session.begin() as session:
            self.assertIsNone(
                consume_login_token(
                    session,
                    expired,
                    expected_admin_id=123,
                    now=datetime(2026, 7, 10, 12, 5, 1),
                )
            )
            self.assertIsNone(
                consume_login_token(
                    session,
                    wrong_admin,
                    expected_admin_id=123,
                    now=datetime(2026, 7, 10, 12, 1, 0),
                )
            )

    def test_admin_support_tables_are_registered(self):
        table_names = set(Base.metadata.tables)

        self.assertIn(AdminAuditLog.__tablename__, table_names)
        self.assertIn(StockAdjustment.__tablename__, table_names)
        self.assertIn(BroadcastJob.__tablename__, table_names)
        self.assertIn(BroadcastDelivery.__tablename__, table_names)

    def test_login_url_keeps_token_out_of_query_string(self):
        self.assertEqual(
            build_login_url("https://bot.example.com/", "secret-token"),
            "https://bot.example.com/admin/login#secret-token",
        )


if __name__ == "__main__":
    unittest.main()
