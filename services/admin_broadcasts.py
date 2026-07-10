"""Database-backed broadcast worker run by the single Telegram bot process."""

from __future__ import annotations

from database import get_db_session
from database.models import BroadcastDelivery, BroadcastJob, User
from services.admin_operations import AdminOperationError, record_audit, utcnow


async def deliver_next_broadcast(bot, session_provider=get_db_session) -> bool:
    """Deliver one queued broadcast and persist per-recipient outcomes."""
    with session_provider() as session:
        job = (
            session.query(BroadcastJob)
            .filter(BroadcastJob.status == "pending")
            .order_by(BroadcastJob.created_at)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            return False
        job.status = "running"
        job.started_at = job.started_at or utcnow()
        job_id = job.id
        delivery_ids = [
            row.id
            for row in session.query(BroadcastDelivery)
            .filter_by(job_id=job.id, status="pending")
            .order_by(BroadcastDelivery.id)
            .all()
        ]

    for delivery_id in delivery_ids:
        with session_provider() as session:
            delivery = session.get(BroadcastDelivery, delivery_id)
            job = session.get(BroadcastJob, job_id)
            user = session.get(User, delivery.user_id) if delivery else None
            if not delivery or delivery.status != "pending" or not user or not job:
                continue
            telegram_id = user.telegram_id
            message_text = job.message_text
            image_path = job.image_path
            delivery.attempts += 1

        try:
            if image_path:
                with open(image_path, "rb") as image:
                    await bot.send_photo(chat_id=telegram_id, photo=image, caption=message_text or None)
            else:
                await bot.send_message(chat_id=telegram_id, text=message_text)
        except Exception as exc:
            with session_provider() as session:
                delivery = session.get(BroadcastDelivery, delivery_id)
                if delivery and delivery.status == "pending":
                    delivery.status = "failed"
                    delivery.error_message = str(exc)[:500]
        else:
            with session_provider() as session:
                delivery = session.get(BroadcastDelivery, delivery_id)
                if delivery and delivery.status == "pending":
                    delivery.status = "sent"
                    delivery.error_message = None
                    delivery.sent_at = utcnow()

    with session_provider() as session:
        job = session.get(BroadcastJob, job_id)
        job.sent_count = session.query(BroadcastDelivery).filter_by(job_id=job_id, status="sent").count()
        job.failed_count = session.query(BroadcastDelivery).filter_by(job_id=job_id, status="failed").count()
        pending = session.query(BroadcastDelivery).filter_by(job_id=job_id, status="pending").count()
        if pending == 0:
            job.status = "completed_with_errors" if job.failed_count else "completed"
            job.completed_at = utcnow()
    return True


def retry_failed_broadcast(session, job_id: int, *, admin_id: int) -> int:
    job = session.query(BroadcastJob).filter_by(id=job_id).with_for_update().first()
    if not job:
        raise AdminOperationError("Broadcast tidak ditemukan.")
    failed = session.query(BroadcastDelivery).filter_by(job_id=job.id, status="failed").all()
    if not failed:
        raise AdminOperationError("Tidak ada pengiriman gagal untuk dicoba ulang.")
    for delivery in failed:
        delivery.status = "pending"
        delivery.error_message = None
    job.status = "pending"
    job.failed_count = 0
    job.completed_at = None
    record_audit(
        session,
        admin_id=admin_id,
        action="broadcast.retry",
        entity_type="broadcast_job",
        entity_id=job.id,
        metadata={"delivery_count": len(failed)},
    )
    return len(failed)


async def process_admin_broadcast_queue(context) -> None:
    """python-telegram-bot JobQueue callback."""
    await deliver_next_broadcast(context.bot)
