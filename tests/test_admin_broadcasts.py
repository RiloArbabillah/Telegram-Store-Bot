import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, BroadcastDelivery, BroadcastJob, User
from services.admin_broadcasts import deliver_next_broadcast, retry_failed_broadcast
from services.admin_operations import create_broadcast_job


class AdminBroadcastWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

        @contextmanager
        def session_provider():
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        self.session_provider = session_provider
        with self.Session.begin() as session:
            session.add_all([User(telegram_id=10), User(telegram_id=20)])
        with self.Session.begin() as session:
            self.job_id = create_broadcast_job(session, "Halo", None, admin_id=123).id

    async def test_worker_records_success_and_failure_per_recipient(self):
        bot = AsyncMock()
        bot.send_message.side_effect = [None, RuntimeError("blocked")]

        processed = await deliver_next_broadcast(bot, self.session_provider)

        self.assertTrue(processed)
        with self.Session() as session:
            job = session.get(BroadcastJob, self.job_id)
            statuses = [row.status for row in session.query(BroadcastDelivery).order_by(BroadcastDelivery.id)]
            self.assertEqual(job.status, "completed_with_errors")
            self.assertEqual(job.sent_count, 1)
            self.assertEqual(job.failed_count, 1)
            self.assertEqual(statuses, ["sent", "failed"])

    async def test_retry_requeues_only_failed_deliveries(self):
        first_bot = AsyncMock()
        first_bot.send_message.side_effect = [None, RuntimeError("blocked")]
        await deliver_next_broadcast(first_bot, self.session_provider)

        with self.Session.begin() as session:
            retried = retry_failed_broadcast(session, self.job_id, admin_id=123)
        self.assertEqual(retried, 1)

        second_bot = AsyncMock()
        await deliver_next_broadcast(second_bot, self.session_provider)

        self.assertEqual(second_bot.send_message.await_count, 1)
        with self.Session() as session:
            job = session.get(BroadcastJob, self.job_id)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.sent_count, 2)
            self.assertEqual(job.failed_count, 0)


if __name__ == "__main__":
    unittest.main()
