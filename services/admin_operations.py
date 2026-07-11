"""Transactional business operations shared by Telegram and web admins."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from database.models import (
    AdminAuditLog,
    BroadcastDelivery,
    BroadcastJob,
    Dispute,
    DisputeStatus,
    Order,
    OrderStatus,
    Product,
    ProductKey,
    StockAdjustment,
    Transaction,
    TransactionStatus,
    User,
)
from services.payments.common import complete_transaction, is_manual_qris_expired


class AdminOperationError(ValueError):
    """Raised when an admin action conflicts with current business state."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def record_audit(
    session,
    *,
    admin_id: int,
    action: str,
    entity_type: str,
    entity_id: int | str | None,
    metadata: dict | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            admin_telegram_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True),
            created_at=utcnow(),
        )
    )


def set_user_banned(session, user_id: int, banned: bool, *, admin_id: int) -> User:
    user = session.get(User, user_id)
    if not user:
        raise AdminOperationError("Pengguna tidak ditemukan.")
    if user.is_banned == banned:
        raise AdminOperationError("Status pengguna sudah sesuai.")
    user.is_banned = banned
    record_audit(
        session,
        admin_id=admin_id,
        action="user.ban" if banned else "user.unban",
        entity_type="user",
        entity_id=user.id,
    )
    return user


def cancel_order(session, order_id: int, *, admin_id: int) -> Order:
    order = session.query(Order).filter_by(id=order_id).with_for_update().first()
    if not order:
        raise AdminOperationError("Pesanan tidak ditemukan.")
    if order.status != OrderStatus.PROCESSING:
        raise AdminOperationError("Hanya pesanan yang sedang diproses yang dapat dibatalkan.")
    user = session.get(User, order.user_id)
    if not user:
        raise AdminOperationError("Pengguna pemilik pesanan tidak ditemukan.")
    user.wallet_balance += order.total_amount
    order.status = OrderStatus.CANCELLED
    record_audit(
        session,
        admin_id=admin_id,
        action="order.cancel",
        entity_type="order",
        entity_id=order.id,
        metadata={"refund_amount": order.total_amount},
    )
    return order


def complete_order(session, order_id: int, *, admin_id: int) -> Order:
    order = session.query(Order).filter_by(id=order_id).with_for_update().first()
    if not order:
        raise AdminOperationError("Pesanan tidak ditemukan.")
    if order.status != OrderStatus.PROCESSING:
        raise AdminOperationError("Status pesanan sudah final.")
    order.status = OrderStatus.COMPLETED
    order.completed_at = utcnow()
    record_audit(
        session,
        admin_id=admin_id,
        action="order.complete",
        entity_type="order",
        entity_id=order.id,
    )
    return order


def confirm_transaction(session, transaction_id: int, *, admin_id: int):
    transaction = session.query(Transaction).filter_by(id=transaction_id).with_for_update().first()
    if not transaction:
        raise AdminOperationError("Transaksi tidak ditemukan.")
    if transaction.status != TransactionStatus.PENDING:
        raise AdminOperationError("Transaksi ini sudah diproses.")
    if is_manual_qris_expired(transaction):
        transaction.status = TransactionStatus.EXPIRED
        raise AdminOperationError("Transaksi QRIS sudah kedaluwarsa.")
    notification = complete_transaction(session, transaction)
    if notification is None:
        raise AdminOperationError("Transaksi tidak dapat dikonfirmasi.")
    record_audit(
        session,
        admin_id=admin_id,
        action="transaction.confirm",
        entity_type="transaction",
        entity_id=transaction.id,
        metadata={"credited_amount": notification.amount},
    )
    return notification


def cancel_transaction(session, transaction_id: int, *, admin_id: int) -> Transaction:
    transaction = session.query(Transaction).filter_by(id=transaction_id).with_for_update().first()
    if not transaction:
        raise AdminOperationError("Transaksi tidak ditemukan.")
    if transaction.status != TransactionStatus.PENDING:
        raise AdminOperationError("Transaksi ini sudah diproses.")
    transaction.status = TransactionStatus.FAILED
    record_audit(
        session,
        admin_id=admin_id,
        action="transaction.cancel",
        entity_type="transaction",
        entity_id=transaction.id,
    )
    return transaction


def resolve_dispute(session, dispute_id: int, notes: str, *, admin_id: int) -> Dispute:
    dispute = session.query(Dispute).filter_by(id=dispute_id).with_for_update().first()
    if not dispute:
        raise AdminOperationError("Sengketa tidak ditemukan.")
    if dispute.status != DisputeStatus.OPENED:
        raise AdminOperationError("Sengketa ini sudah diselesaikan.")
    order = session.get(Order, dispute.order_id)
    dispute.status = DisputeStatus.RESOLVED
    dispute.admin_notes = notes.strip() or None
    dispute.resolved_at = utcnow()
    if order:
        order.dispute_status = DisputeStatus.RESOLVED
    record_audit(
        session,
        admin_id=admin_id,
        action="dispute.resolve",
        entity_type="dispute",
        entity_id=dispute.id,
    )
    return dispute


def restock_product(
    session,
    product_id: int,
    key_values: list[str],
    *,
    admin_id: int,
    supporting_files: str | None = None,
    source: str = "web",
) -> int:
    product = session.query(Product).filter_by(id=product_id).with_for_update().first()
    if not product:
        raise AdminOperationError("Produk tidak ditemukan.")
    normalized = list(dict.fromkeys(value.strip() for value in key_values if value.strip()))
    if not normalized:
        raise AdminOperationError("Masukkan minimal satu item stok.")
    for key_value in normalized:
        session.add(
            ProductKey(
                product_id=product.id,
                key_value=key_value,
                supporting_files=supporting_files,
                is_sold=False,
            )
        )
    product.stock_count = int(product.stock_count or 0) + len(normalized)
    session.add(
        StockAdjustment(
            product_id=product.id,
            adjustment_type="restock",
            quantity=len(normalized),
            source=source,
            admin_telegram_id=admin_id,
            created_at=utcnow(),
        )
    )
    record_audit(
        session,
        admin_id=admin_id,
        action="product.restock",
        entity_type="product",
        entity_id=product.id,
        metadata={"quantity": len(normalized), "source": source},
    )
    return len(normalized)


def create_broadcast_job(
    session,
    message_text: str,
    image_path: str | None,
    *,
    admin_id: int,
) -> BroadcastJob:
    text = message_text.strip()
    if not text and not image_path:
        raise AdminOperationError("Broadcast harus memiliki teks atau gambar.")
    users = session.query(User).filter_by(is_banned=False).order_by(User.id).all()
    job = BroadcastJob(
        admin_telegram_id=admin_id,
        message_text=text,
        image_path=image_path,
        status="pending",
        target_count=len(users),
        created_at=utcnow(),
    )
    session.add(job)
    session.flush()
    for user in users:
        session.add(BroadcastDelivery(job_id=job.id, user_id=user.id, status="pending"))
    record_audit(
        session,
        admin_id=admin_id,
        action="broadcast.create",
        entity_type="broadcast_job",
        entity_id=job.id,
        metadata={"target_count": len(users), "has_image": bool(image_path)},
    )
    return job
