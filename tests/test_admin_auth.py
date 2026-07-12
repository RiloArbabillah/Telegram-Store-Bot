import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    AdminAuditLog,
    AdminOtpCode,
    BroadcastDelivery,
    BroadcastJob,
    Base,
    StockAdjustment,
)
from services.admin_auth import create_admin_otp, consume_admin_otp


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_creates_hashed_eight_digit_otp(self):
        with patch("services.admin_auth.secrets.randbelow", return_value=42):
            with self.Session.begin() as session:
                otp = create_admin_otp(
                    session,
                    123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 0, 0),
                )
                record = session.query(AdminOtpCode).one()
                code_hash = record.code_hash
                admin_telegram_id = record.admin_telegram_id
                expires_at = record.expires_at
                attempt_count = record.attempt_count

        self.assertEqual(otp, "00000042")
        self.assertNotEqual(code_hash, otp)
        self.assertEqual(len(code_hash), 64)
        self.assertEqual(admin_telegram_id, 123)
        self.assertEqual(expires_at, datetime(2026, 7, 10, 12, 5, 0))
        self.assertEqual(attempt_count, 0)

    def test_consumes_valid_otp_only_once(self):
        with self.Session.begin() as session:
            otp = create_admin_otp(
                session,
                123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 0, 0),
            )

        with self.Session.begin() as session:
            admin_id = consume_admin_otp(
                session,
                otp,
                expected_admin_id=123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 1, 0),
            )
        self.assertEqual(admin_id, 123)

        with self.Session.begin() as session:
            replay = consume_admin_otp(
                session,
                otp,
                expected_admin_id=123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 2, 0),
            )
        self.assertIsNone(replay)

    def test_rejects_expired_or_wrong_admin_otp(self):
        with self.Session.begin() as session:
            expired = create_admin_otp(
                session,
                123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 0, 0),
            )
            wrong_admin = create_admin_otp(
                session,
                456,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 0, 0),
            )

        with self.Session.begin() as session:
            self.assertIsNone(
                consume_admin_otp(
                    session,
                    expired,
                    expected_admin_id=123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 5, 1),
                )
            )
            self.assertIsNone(
                consume_admin_otp(
                    session,
                    wrong_admin,
                    expected_admin_id=123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 1, 0),
                )
            )

    def test_new_otp_revokes_previous_active_otp(self):
        with self.Session.begin() as session:
            first = create_admin_otp(
                session,
                123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 0, 0),
            )
            second = create_admin_otp(
                session,
                123,
                secret="session-secret-for-tests",
                now=datetime(2026, 7, 10, 12, 1, 0),
            )

        with self.Session.begin() as session:
            self.assertIsNone(
                consume_admin_otp(
                    session,
                    first,
                    expected_admin_id=123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 2, 0),
                )
            )
            self.assertEqual(
                consume_admin_otp(
                    session,
                    second,
                    expected_admin_id=123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 2, 0),
                ),
                123,
            )

    def test_otp_is_revoked_after_five_failed_attempts(self):
        with patch("services.admin_auth.secrets.randbelow", return_value=22_222_222):
            with self.Session.begin() as session:
                otp = create_admin_otp(
                    session,
                    123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 0, 0),
                )

        for _ in range(5):
            with self.Session.begin() as session:
                self.assertIsNone(
                    consume_admin_otp(
                        session,
                        "11111111",
                        expected_admin_id=123,
                        secret="session-secret-for-tests",
                        now=datetime(2026, 7, 10, 12, 1, 0),
                    )
                )

        with self.Session.begin() as session:
            self.assertIsNone(
                consume_admin_otp(
                    session,
                    otp,
                    expected_admin_id=123,
                    secret="session-secret-for-tests",
                    now=datetime(2026, 7, 10, 12, 2, 0),
                )
            )

    def test_admin_support_tables_are_registered(self):
        table_names = set(Base.metadata.tables)

        self.assertIn(AdminOtpCode.__tablename__, table_names)
        self.assertIn(AdminAuditLog.__tablename__, table_names)
        self.assertIn(StockAdjustment.__tablename__, table_names)
        self.assertIn(BroadcastJob.__tablename__, table_names)
        self.assertIn(BroadcastDelivery.__tablename__, table_names)


if __name__ == "__main__":
    unittest.main()
