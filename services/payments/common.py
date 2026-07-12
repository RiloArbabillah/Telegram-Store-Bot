"""Shared helpers for normalized payment transactions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from database import PaymentMethod, TransactionStatus
from services.direct_checkout import finalize_paid_checkout

from .base import PaymentNotification


PAYMENT_METHOD_LABELS = {
    PaymentMethod.CRYPTO_WALLET: "CryptoBot",
    PaymentMethod.CARD: "Card",
    PaymentMethod.QRIS: "QRIS",
}

MANUAL_QRIS_EXPIRY_MINUTES = 15
QRIS_MESSAGE_REFS_KEY = "telegram_qris_messages"


def payment_method_label(payment_method: PaymentMethod) -> str:
    """Return a user-facing label for a payment method."""
    return PAYMENT_METHOD_LABELS.get(payment_method, payment_method.value.replace("_", " ").title())


def parse_provider_metadata(raw_metadata: str | None) -> dict:
    """Parse provider metadata stored as JSON text."""
    if not raw_metadata:
        return {}

    try:
        parsed = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def dump_provider_metadata(metadata: dict | None) -> str | None:
    """Serialize provider metadata to JSON text."""
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True)


def get_qris_message_refs(transaction) -> list[dict[str, int]]:
    """Return valid Telegram QRIS message references stored on a transaction."""
    metadata = parse_provider_metadata(transaction.provider_metadata)
    refs = metadata.get(QRIS_MESSAGE_REFS_KEY, [])
    if not isinstance(refs, list):
        return []

    valid_refs = []
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        try:
            normalized = {
                "chat_id": int(ref["chat_id"]),
                "message_id": int(ref["message_id"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        key = (normalized["chat_id"], normalized["message_id"])
        if key not in seen:
            valid_refs.append(normalized)
            seen.add(key)
    return valid_refs


def register_qris_message_ref(transaction, *, chat_id: int, message_id: int) -> None:
    """Register one Telegram payment message for later terminal-state cleanup."""
    refs = get_qris_message_refs(transaction)
    new_ref = {"chat_id": int(chat_id), "message_id": int(message_id)}
    if new_ref not in refs:
        refs.append(new_ref)

    metadata = parse_provider_metadata(transaction.provider_metadata)
    metadata[QRIS_MESSAGE_REFS_KEY] = refs
    transaction.provider_metadata = dump_provider_metadata(metadata)


def retain_qris_message_refs(transaction, refs: list[dict[str, int]]) -> None:
    """Replace the active QRIS message references after a cleanup attempt."""
    metadata = parse_provider_metadata(transaction.provider_metadata)
    metadata[QRIS_MESSAGE_REFS_KEY] = refs
    transaction.provider_metadata = dump_provider_metadata(metadata)


def extract_checkout_url(transaction) -> str | None:
    """Return the canonical checkout URL for a transaction."""
    if transaction.checkout_url:
        return transaction.checkout_url

    if transaction.crypto_address and "|" in transaction.crypto_address:
        _, pay_url = transaction.crypto_address.split("|", 1)
        return pay_url

    if transaction.crypto_address and transaction.crypto_address.startswith("http"):
        return transaction.crypto_address

    return None


def extract_external_reference(transaction) -> str | None:
    """Return the canonical external reference for a transaction."""
    if transaction.external_reference:
        return transaction.external_reference

    if transaction.crypto_address and "|" in transaction.crypto_address:
        invoice_id, _ = transaction.crypto_address.split("|", 1)
        return invoice_id

    if transaction.crypto_address and transaction.crypto_address.startswith("tg_charge:"):
        return transaction.crypto_address.split(":", 1)[1]

    if transaction.crypto_address and not transaction.crypto_address.startswith("http"):
        return transaction.crypto_address

    return None


def hydrate_legacy_transaction(transaction) -> None:
    """Populate normalized transaction fields from legacy storage when needed."""
    if not transaction.provider_name:
        if transaction.payment_method == PaymentMethod.CRYPTO_WALLET:
            transaction.provider_name = "cryptobot"
        elif transaction.payment_method == PaymentMethod.CARD:
            transaction.provider_name = "telegram_payments"
        elif transaction.payment_method == PaymentMethod.QRIS:
            transaction.provider_name = "qris"

    if not transaction.external_reference:
        transaction.external_reference = extract_external_reference(transaction)

    if not transaction.checkout_url:
        transaction.checkout_url = extract_checkout_url(transaction)


def update_transaction_provider_fields(
    transaction,
    *,
    provider_name: str | None = None,
    external_reference: str | None = None,
    checkout_url: str | None = None,
    qr_payload: str | None = None,
    provider_metadata: dict | None = None,
    legacy_reference: str | None = None,
) -> None:
    """Update normalized provider fields and preserve legacy compatibility."""
    if provider_name:
        transaction.provider_name = provider_name
    if external_reference:
        transaction.external_reference = str(external_reference)
    if checkout_url:
        transaction.checkout_url = checkout_url
    if qr_payload:
        transaction.qr_payload = qr_payload
    if provider_metadata is not None:
        merged_metadata = parse_provider_metadata(transaction.provider_metadata)
        merged_metadata.update(provider_metadata)
        transaction.provider_metadata = dump_provider_metadata(merged_metadata)
    if legacy_reference:
        transaction.crypto_address = legacy_reference


def is_manual_qris_expired(transaction, *, now: datetime | None = None) -> bool:
    """Return True once a manual QRIS transaction is past its expiry window."""
    if transaction.payment_method != PaymentMethod.QRIS or transaction.provider_name == "dana_qris":
        return False

    now = now or datetime.utcnow()
    expiry_candidates = []
    if transaction.expires_at:
        expiry_candidates.append(transaction.expires_at)
    if transaction.created_at:
        expiry_candidates.append(transaction.created_at + timedelta(minutes=MANUAL_QRIS_EXPIRY_MINUTES))

    return bool(expiry_candidates and now > min(expiry_candidates))


def complete_transaction(
    session,
    transaction,
    *,
    credited_amount: int | None = None,
    provider_name: str | None = None,
    external_reference: str | None = None,
    checkout_url: str | None = None,
    qr_payload: str | None = None,
    provider_metadata: dict | None = None,
) -> PaymentNotification | None:
    """Mark a transaction complete exactly once and finalize the linked order."""
    hydrate_legacy_transaction(transaction)

    if transaction.status == TransactionStatus.COMPLETED:
        return None

    update_transaction_provider_fields(
        transaction,
        provider_name=provider_name,
        external_reference=external_reference,
        checkout_url=checkout_url,
        qr_payload=qr_payload,
        provider_metadata=provider_metadata,
    )

    if credited_amount is not None:
        transaction.confirmed_amount = int(credited_amount)

    if transaction.order_id:
        delivery = finalize_paid_checkout(session, transaction.id)
        if not delivery:
            return None
        return PaymentNotification(
            user_telegram_id=delivery.user_telegram_id,
            amount=delivery.amount,
            transaction_id=transaction.id,
            payment_method=payment_method_label(transaction.payment_method),
            provider_name=transaction.provider_name,
            order_id=delivery.order_id,
            order_details=delivery.order_details,
            supporting_files=delivery.supporting_files,
        )

    effective_amount = credited_amount if credited_amount is not None else transaction.confirmed_amount
    if effective_amount is None:
        effective_amount = transaction.amount
    effective_amount = int(effective_amount)
    transaction.confirmed_amount = effective_amount
    transaction.status = TransactionStatus.COMPLETED
    transaction.completed_at = datetime.utcnow()

    return PaymentNotification(
        user_telegram_id=transaction.user.telegram_id,
        amount=effective_amount,
        transaction_id=transaction.id,
        payment_method=payment_method_label(transaction.payment_method),
        provider_name=transaction.provider_name,
    )
