import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from database import PaymentMethod
from services.payments.common import is_manual_qris_expired


class ManualQrisExpiryTests(unittest.TestCase):
    def test_manual_qris_expires_after_fifteen_minutes(self):
        created_at = datetime(2026, 7, 10, 12, 0, 0)
        transaction = SimpleNamespace(
            payment_method=PaymentMethod.QRIS,
            provider_name="qris",
            created_at=created_at,
            expires_at=None,
        )

        self.assertFalse(
            is_manual_qris_expired(
                transaction,
                now=created_at + timedelta(minutes=14, seconds=59),
            )
        )
        self.assertTrue(
            is_manual_qris_expired(
                transaction,
                now=created_at + timedelta(minutes=15, seconds=1),
            )
        )


if __name__ == "__main__":
    unittest.main()
