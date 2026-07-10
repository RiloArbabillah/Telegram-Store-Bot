import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, NetworkError

from database import PaymentMethod
from services.payments.common import (
    get_qris_message_refs,
    register_qris_message_ref,
)
from services.payments.qris_messages import cleanup_qris_messages


class QrisMessageMetadataTests(unittest.TestCase):
    def test_registers_and_deduplicates_message_references(self):
        transaction = SimpleNamespace(provider_metadata=None)

        register_qris_message_ref(transaction, chat_id=10, message_id=20)
        register_qris_message_ref(transaction, chat_id=10, message_id=20)
        register_qris_message_ref(transaction, chat_id=10, message_id=21)

        self.assertEqual(
            get_qris_message_refs(transaction),
            [
                {"chat_id": 10, "message_id": 20},
                {"chat_id": 10, "message_id": 21},
            ],
        )


class QrisMessageCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transaction = SimpleNamespace(
            id=123,
            payment_method=PaymentMethod.QRIS,
            provider_metadata=None,
        )
        register_qris_message_ref(self.transaction, chat_id=10, message_id=20)
        register_qris_message_ref(self.transaction, chat_id=10, message_id=21)

    def fake_session_factory(self):
        transaction = self.transaction

        class Query:
            def filter_by(self, **kwargs):
                return self

            def first(self):
                return transaction

        class Session:
            def query(self, model):
                return Query()

        @contextmanager
        def session_scope():
            yield Session()

        return session_scope

    async def test_successful_cleanup_removes_all_references(self):
        bot = SimpleNamespace(delete_message=AsyncMock(return_value=True))

        with patch(
            "services.payments.qris_messages.get_db_session",
            self.fake_session_factory(),
        ):
            result = await cleanup_qris_messages(bot, self.transaction.id)

        self.assertTrue(result)
        self.assertEqual(get_qris_message_refs(self.transaction), [])
        self.assertEqual(bot.delete_message.await_count, 2)

    async def test_missing_message_is_treated_as_already_cleaned(self):
        bot = SimpleNamespace(
            delete_message=AsyncMock(side_effect=BadRequest("Message to delete not found"))
        )

        with patch(
            "services.payments.qris_messages.get_db_session",
            self.fake_session_factory(),
        ):
            result = await cleanup_qris_messages(bot, self.transaction.id)

        self.assertTrue(result)
        self.assertEqual(get_qris_message_refs(self.transaction), [])

    async def test_transient_failure_keeps_references_for_retry(self):
        bot = SimpleNamespace(delete_message=AsyncMock(side_effect=NetworkError("temporary")))

        with patch(
            "services.payments.qris_messages.get_db_session",
            self.fake_session_factory(),
        ):
            result = await cleanup_qris_messages(bot, self.transaction.id)

        self.assertFalse(result)
        self.assertEqual(len(get_qris_message_refs(self.transaction)), 2)


if __name__ == "__main__":
    unittest.main()
