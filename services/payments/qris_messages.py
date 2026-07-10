"""Telegram message lifecycle helpers for QRIS payment instructions."""

from __future__ import annotations

import logging

import requests
from telegram.error import BadRequest

from config.settings import settings
from database import PaymentMethod, Transaction, get_db_session

from .common import get_qris_message_refs, retain_qris_message_refs


logger = logging.getLogger(__name__)


def _is_already_deleted_error(error: object) -> bool:
    text = str(error).lower()
    return "message to delete not found" in text


def _remove_deleted_refs(transaction_id: int, deleted_refs: list[dict[str, int]]) -> None:
    if not deleted_refs:
        return

    deleted_keys = {(ref["chat_id"], ref["message_id"]) for ref in deleted_refs}
    with get_db_session() as session:
        transaction = session.query(Transaction).filter_by(id=transaction_id).first()
        if not transaction:
            return
        remaining = [
            ref for ref in get_qris_message_refs(transaction)
            if (ref["chat_id"], ref["message_id"]) not in deleted_keys
        ]
        retain_qris_message_refs(transaction, remaining)


async def cleanup_qris_messages(bot, transaction_id: int) -> bool:
    """Delete tracked QRIS messages with Telegram's async bot client."""
    with get_db_session() as session:
        transaction = session.query(Transaction).filter_by(id=transaction_id).first()
        if not transaction or transaction.payment_method != PaymentMethod.QRIS:
            return True
        refs = get_qris_message_refs(transaction)

    deleted_refs = []
    for ref in refs:
        try:
            await bot.delete_message(**ref)
            deleted_refs.append(ref)
        except BadRequest as exc:
            if _is_already_deleted_error(exc):
                deleted_refs.append(ref)
            else:
                logger.warning("Failed to delete QRIS message for transaction %s: %s", transaction_id, exc)
        except Exception as exc:
            logger.warning("Failed to delete QRIS message for transaction %s: %s", transaction_id, exc)

    _remove_deleted_refs(transaction_id, deleted_refs)
    return len(deleted_refs) == len(refs)


def cleanup_qris_messages_sync(transaction_id: int) -> bool:
    """Delete tracked QRIS messages from the synchronous webhook process."""
    with get_db_session() as session:
        transaction = session.query(Transaction).filter_by(id=transaction_id).first()
        if not transaction or transaction.payment_method != PaymentMethod.QRIS:
            return True
        refs = get_qris_message_refs(transaction)

    if not settings.BOT_TOKEN:
        return not refs

    deleted_refs = []
    for ref in refs:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/deleteMessage",
                json=ref,
                timeout=10,
            )
            if response.ok or _is_already_deleted_error(response.text):
                deleted_refs.append(ref)
            else:
                logger.warning(
                    "Failed to delete QRIS message for transaction %s: HTTP %s %s",
                    transaction_id,
                    response.status_code,
                    response.text,
                )
        except requests.RequestException as exc:
            logger.warning("Failed to delete QRIS message for transaction %s: %s", transaction_id, exc)

    _remove_deleted_refs(transaction_id, deleted_refs)
    return len(deleted_refs) == len(refs)
