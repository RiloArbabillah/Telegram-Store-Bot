"""QRIS provider with manual fallback and optional DANA API mode."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from config.settings import settings as app_settings
from database import PaymentMethod, Settings, Transaction, TransactionStatus
from services.direct_checkout import expire_pending_checkout
from utils import calculate_expiry_time, format_price

from .base import PaymentCreationError, PaymentPage, PaymentProvider, PaymentWebhookResult
from .common import (
    MANUAL_QRIS_EXPIRY_MINUTES,
    complete_transaction,
    is_manual_qris_expired,
    parse_provider_metadata,
    update_transaction_provider_fields,
)
from .dana_client import DanaClientError, generate_qris, query_payment, verify_callback_signature
from .qris_static import QrisPayloadError, generate_dynamic_qris, parse_tlv, render_qris_png_bytes

logger = logging.getLogger(__name__)

DANA_ACTIVE_PROVIDERS = {"dana_qris"}
MANUAL_QRIS_UNIQUE_CODE_MAX = 500


class QrisProvider(PaymentProvider):
    """Dual-mode QRIS provider: manual fallback or DANA API."""

    method = PaymentMethod.QRIS
    provider_name = "qris"
    button_label = "📱 QRIS"

    def is_available(self) -> bool:
        return True

    def create_payment(self, session, user, amount: float, *, order_id: int | None = None):
        if app_settings.DANA_ENABLED:
            return self._create_dana_payment(session, user, amount, order_id=order_id)

        return self._create_manual_payment(session, user, amount, order_id=order_id)
        settings = session.query(Settings).first()
        instructions_text = settings.qris_instructions_text.strip() if settings and settings.qris_instructions_text else ""
        image_file_id = settings.qris_image_file_id if settings and settings.qris_image_file_id else None

        if not instructions_text:
            raise PaymentCreationError(
                "❌ QRIS manual payment is not configured yet.\n\nAdmin needs to set QRIS instructions first."
            )

        existing_pending = session.query(Transaction).filter_by(
            user_id=user.id,
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).order_by(Transaction.created_at.desc()).first()

        if existing_pending:
            expiry_text = existing_pending.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if existing_pending.expires_at else 'N/A'
            message = f"""📱 QRIS Payment

💰 Amount: {format_price(existing_pending.amount)}
🆔 Order ID: #{existing_pending.id}

Transfer the exact amount using the QRIS details below:

{instructions_text}

After you finish the transfer, send the payment proof in this chat as a photo or document.

⏰ Expires: {expiry_text}"""
            return existing_pending, PaymentPage(message=message, photo_file_id=image_file_id)

        transaction = Transaction(
            user_id=user.id,
            amount=amount,
            payment_method=self.method,
            provider_name=self.provider_name,
            status=TransactionStatus.PENDING,
            expires_at=calculate_expiry_time(app_settings.PAYMENT_EXPIRY_HOURS),
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)

        manual_reference = f"qris-manual-{transaction.id}"
        update_transaction_provider_fields(
            transaction,
            provider_name=self.provider_name,
            external_reference=manual_reference,
            qr_payload=instructions_text,
            provider_metadata={"mode": "manual_review"},
        )
        session.commit()

        expiry_text = transaction.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if transaction.expires_at else 'N/A'
        message = f"""📱 QRIS Payment

💰 Amount: {format_price(amount)}
🆔 Order ID: #{transaction.id}

Transfer the exact amount using the QRIS details below:

{instructions_text}

After you finish the transfer, send the payment proof in this chat as a photo or document.

⏰ Expires: {expiry_text}"""

        return transaction, PaymentPage(message=message, photo_file_id=image_file_id)

    def poll_transaction(self, session, transaction):
        if not self._is_dana_transaction(transaction):
            return None

        partner_reference_no = transaction.external_reference
        if not partner_reference_no:
            return None

        # Give callbacks a head-start: skip polling very fresh transactions.
        if self._is_fresh_transaction(transaction):
            return None

        try:
            result = query_payment(partner_reference_no=partner_reference_no)
        except DanaClientError:
            logger.exception("DANA query failed for transaction %s", transaction.id)
            return None

        self._apply_dana_status(session, transaction, result.status_code, result.payload, source="poll")

        if transaction.provider_paid_at and transaction.status != TransactionStatus.COMPLETED:
            return complete_transaction(
                session,
                transaction,
                provider_name="dana_qris",
                external_reference=transaction.external_reference,
                checkout_url=transaction.checkout_url,
                provider_metadata={"finalized_via": "poll"},
            )

        session.commit()
        return None

    def process_webhook(self, session, payload: dict):
        payload = payload if isinstance(payload, dict) else {}
        headers = payload.pop("__headers", {})

        if not verify_callback_signature(headers=headers, body_bytes=payload.get("__raw", b"")):
            return PaymentWebhookResult(handled=False)

        partner_reference_no = payload.get("originalPartnerReferenceNo") or payload.get("partnerReferenceNo")
        status_code = str(payload.get("latestTransactionStatus") or payload.get("transactionStatus") or "")

        transaction = None
        if partner_reference_no:
            transaction = session.query(Transaction).filter_by(
                payment_method=self.method,
                external_reference=str(partner_reference_no),
            ).first()

        if not transaction:
            logger.warning("No DANA transaction found for callback reference %s", partner_reference_no)
            return PaymentWebhookResult(handled=False)

        # If already terminal, acknowledge without re-processing.
        if transaction.status in {
            TransactionStatus.COMPLETED,
            TransactionStatus.FAILED,
            TransactionStatus.EXPIRED,
        }:
            logger.debug("DANA callback for already-terminal transaction %s", transaction.id)
            return PaymentWebhookResult(handled=True)

        self._apply_dana_status(session, transaction, status_code, payload, source="callback")

        if transaction.provider_paid_at and transaction.status != TransactionStatus.COMPLETED:
            notification = complete_transaction(
                session,
                transaction,
                provider_name="dana_qris",
                external_reference=transaction.external_reference,
                checkout_url=transaction.checkout_url,
                provider_metadata={"finalized_via": "callback"},
            )
            return PaymentWebhookResult(handled=True, notification=notification)

        session.commit()
        return PaymentWebhookResult(handled=True)

    def _is_dana_transaction(self, transaction) -> bool:
        return transaction.provider_name in DANA_ACTIVE_PROVIDERS or (
            isinstance(transaction.provider_metadata, str) and "dana_api" in transaction.provider_metadata
        )

    def _is_fresh_transaction(self, transaction, *, seconds: int = 30) -> bool:
        """Return True if the transaction was created within the last *seconds* seconds."""
        from datetime import datetime, timedelta

        if not transaction.created_at:
            return False
        return transaction.created_at > datetime.utcnow() - timedelta(seconds=seconds)

    def _create_dana_payment(self, session, user, amount: float, *, order_id: int | None = None):
        pending_query = session.query(Transaction).filter_by(
            user_id=user.id,
            payment_method=self.method,
            status=TransactionStatus.PENDING,
            provider_name="dana_qris",
        )
        pending_query = pending_query.filter_by(order_id=order_id) if order_id else pending_query.filter(Transaction.order_id == None)
        pending_transaction = pending_query.order_by(Transaction.created_at.desc()).first()

        if pending_transaction:
            qr_content = pending_transaction.qr_payload or "Scan the stored QRIS code."
            checkout_url = pending_transaction.checkout_url or ""
            expiry_text = pending_transaction.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if pending_transaction.expires_at else 'N/A'
            message = self._dana_page_message(
                transaction_id=pending_transaction.id,
                amount=pending_transaction.amount,
                qr_content=qr_content,
                checkout_url=checkout_url,
                expiry_text=expiry_text,
            )
            button_url = checkout_url if checkout_url else None
            return pending_transaction, PaymentPage(message=message, button_text="Open payment page" if button_url else None, button_url=button_url)

        partner_reference_no = self._build_partner_reference_no()
        transaction = Transaction(
            user_id=user.id,
            order_id=order_id,
            amount=amount,
            payment_method=self.method,
            provider_name="dana_qris",
            status=TransactionStatus.PENDING,
            expires_at=calculate_expiry_time(app_settings.PAYMENT_EXPIRY_HOURS),
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)

        try:
            qr_result = generate_qris(
                partner_reference_no=partner_reference_no,
                amount_idr=amount,
            )
        except DanaClientError:
            session.delete(transaction)
            session.commit()
            raise PaymentCreationError("❌ Failed to create QRIS payment. Please try again.")

        update_transaction_provider_fields(
            transaction,
            provider_name="dana_qris",
            external_reference=partner_reference_no,
            checkout_url=qr_result.qr_url or qr_result.redirect_url,
            qr_payload=qr_result.qr_content,
            provider_metadata={
                "mode": "dana_api",
                "reference_no": qr_result.reference_no,
                "qr_content": qr_result.qr_content,
                "qr_url": qr_result.qr_url,
                "qr_image": qr_result.qr_image,
                "redirect_url": qr_result.redirect_url,
                "raw": qr_result.payload,
            },
        )
        session.commit()

        expiry_text = transaction.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if transaction.expires_at else 'N/A'
        message = self._dana_page_message(
            transaction_id=transaction.id,
            amount=transaction.amount,
            qr_content=qr_result.qr_content,
            checkout_url=qr_result.qr_url or qr_result.redirect_url,
            expiry_text=expiry_text,
        )
        button_url = qr_result.qr_url or qr_result.redirect_url
        return transaction, PaymentPage(message=message, button_text="Open payment page" if button_url else None, button_url=button_url)

    def _create_manual_payment(self, session, user, amount: float, *, order_id: int | None = None):
        admin_settings = session.query(Settings).first()
        instructions_text = admin_settings.qris_instructions_text.strip() if admin_settings and admin_settings.qris_instructions_text else ""
        static_payload = admin_settings.qris_static_payload.strip() if admin_settings and admin_settings.qris_static_payload else ""

        if not instructions_text or not static_payload:
            raise PaymentCreationError(
                "❌ QRIS manual payment is not configured yet.\n\nAdmin needs to upload a QRIS image and set payment instructions first."
            )

        self._expire_stale_manual_qris(session)

        existing_query = session.query(Transaction).filter_by(
            user_id=user.id,
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).filter((Transaction.provider_name == None) | (Transaction.provider_name != 'dana_qris'))
        existing_query = existing_query.filter_by(order_id=order_id) if order_id else existing_query.filter(Transaction.order_id == None)
        existing_pending = existing_query.order_by(Transaction.created_at.desc()).first()

        if existing_pending:
            expiry_text = existing_pending.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if existing_pending.expires_at else 'N/A'
            metadata = parse_provider_metadata(existing_pending.provider_metadata)
            unique_code = metadata.get("unique_code")
            payable_amount = metadata.get("payable_amount")
            metadata_missing = unique_code is None or payable_amount is None
            if unique_code is None or payable_amount is None:
                unique_code = self._generate_unique_code(session, int(existing_pending.amount))
                payable_amount = int(existing_pending.amount) + int(unique_code)
            unique_code = int(unique_code)
            payable_amount = int(payable_amount)

            try:
                parse_tlv(existing_pending.qr_payload or "")
            except QrisPayloadError:
                existing_pending.qr_payload = None

            if metadata_missing or not existing_pending.qr_payload:
                try:
                    if metadata_missing or not existing_pending.qr_payload:
                        existing_pending.qr_payload = generate_dynamic_qris(static_payload, int(payable_amount))
                    update_transaction_provider_fields(
                        existing_pending,
                        provider_name=self.provider_name,
                        provider_metadata={
                            "mode": "manual_review",
                            "unique_code": int(unique_code),
                            "payable_amount": int(payable_amount),
                        },
                    )
                    session.commit()
                except QrisPayloadError as exc:
                    raise PaymentCreationError(f"❌ Failed to generate QRIS payment: {exc}") from exc
            message = f"""📱 QRIS Payment

            💰 Amount: {format_price(existing_pending.amount)}
            🔢 Unique Code: {unique_code:03d}
            ✅ Pay Exactly: {format_price(payable_amount)}
            🆔 Order ID: #{existing_pending.id}

            Scan the QRIS code below and pay the exact amount.

            {instructions_text}

            After you finish the transfer, send the payment proof in this chat as a photo or document.

            ⏰ Expires: {expiry_text}"""
            try:
                photo_bytes = render_qris_png_bytes(existing_pending.qr_payload)
            except QrisPayloadError as exc:
                raise PaymentCreationError(f"❌ Failed to render QRIS payment: {exc}") from exc

            return existing_pending, PaymentPage(
                message=message,
                photo_bytes=photo_bytes,
                photo_filename=f"qris-{existing_pending.id}.png",
            )

        unique_code = self._generate_unique_code(session, int(amount))
        payable_amount = int(amount) + unique_code
        manual_reference = f"qris-manual-{random.randrange(10**8, 10**9)}"

        try:
            dynamic_payload = generate_dynamic_qris(static_payload, payable_amount)
            photo_bytes = render_qris_png_bytes(dynamic_payload)
        except QrisPayloadError as exc:
            raise PaymentCreationError(f"❌ Failed to generate QRIS payment: {exc}") from exc

        transaction = Transaction(
            user_id=user.id,
            order_id=order_id,
            amount=amount,
            payment_method=self.method,
            provider_name=self.provider_name,
            status=TransactionStatus.PENDING,
            expires_at=self._manual_qris_expiry_time(),
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)

        update_transaction_provider_fields(
            transaction,
            provider_name=self.provider_name,
            external_reference=manual_reference,
            qr_payload=dynamic_payload,
            provider_metadata={
                "mode": "manual_review",
                "unique_code": unique_code,
                "payable_amount": payable_amount,
            },
        )
        session.commit()

        expiry_text = transaction.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if transaction.expires_at else 'N/A'
        message = f"""📱 QRIS Payment

        💰 Amount: {format_price(amount)}
        🔢 Unique Code: {unique_code:03d}
        ✅ Pay Exactly: {format_price(payable_amount)}
        🆔 Order ID: #{transaction.id}

        Scan the QRIS code below and pay the exact amount.

        {instructions_text}

        After you finish the transfer, send the payment proof in this chat as a photo or document.

        ⏰ Expires: {expiry_text}"""

        return transaction, PaymentPage(
            message=message,
            photo_bytes=photo_bytes,
            photo_filename=f"qris-{transaction.id}.png",
        )

    def _generate_unique_code(self, session, base_amount: int) -> int:
        self._expire_stale_manual_qris(session)

        used_codes = set()
        pending_transactions = session.query(Transaction).filter_by(
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).filter((Transaction.provider_name == None) | (Transaction.provider_name != 'dana_qris')).all()

        for transaction in pending_transactions:
            metadata = parse_provider_metadata(transaction.provider_metadata)
            unique_code = metadata.get("unique_code")
            if unique_code is not None:
                try:
                    used_codes.add(int(unique_code))
                except (TypeError, ValueError):
                    continue

        candidates = list(range(1, MANUAL_QRIS_UNIQUE_CODE_MAX + 1))
        random.shuffle(candidates)
        for code in candidates:
            if code not in used_codes:
                return code

        raise PaymentCreationError("❌ Too many pending QRIS payments. Please try again later.")

    def _manual_qris_expiry_time(self):
        return datetime.utcnow() + timedelta(minutes=MANUAL_QRIS_EXPIRY_MINUTES)

    def _expire_stale_manual_qris(self, session) -> None:
        now = datetime.utcnow()
        expired = False
        pending_transactions = session.query(Transaction).filter_by(
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).filter((Transaction.provider_name == None) | (Transaction.provider_name != 'dana_qris')).all()

        for transaction in pending_transactions:
            if transaction.created_at:
                created_at_expiry = transaction.created_at + timedelta(minutes=MANUAL_QRIS_EXPIRY_MINUTES)
                if not transaction.expires_at or created_at_expiry < transaction.expires_at:
                    transaction.expires_at = created_at_expiry
                    expired = True

            if is_manual_qris_expired(transaction, now=now):
                if transaction.order_id:
                    expire_pending_checkout(session, transaction.id)
                else:
                    transaction.status = TransactionStatus.EXPIRED
                expired = True

        if expired:
            session.commit()

    def _build_partner_reference_no(self) -> str:
        import uuid
        return f"QR{uuid.uuid4().hex}"[:25]

    def _apply_dana_status(self, session, transaction, status_code: str, payload: dict, *, source: str) -> None:
        from datetime import datetime

        # Guard: never overwrite terminal states from duplicate callbacks/polls.
        if transaction.status in {
            TransactionStatus.COMPLETED,
            TransactionStatus.FAILED,
            TransactionStatus.EXPIRED,
        }:
            logger.debug(
                "Skipping DANA status update for terminal transaction %s (status=%s)",
                transaction.id,
                transaction.status,
            )
            return

        # Strip transport-only fields before persisting as metadata.
        clean_payload = {
            k: v for k, v in payload.items() if k not in {"__raw", "__headers"}
        }

        update_transaction_provider_fields(
            transaction,
            provider_name="dana_qris",
            provider_metadata={
                "last_status_code": status_code,
                "last_status_source": source,
                f"last_{source}_payload": clean_payload,
            },
        )
        transaction.provider_status_code = status_code or transaction.provider_status_code
        transaction.provider_status_text = clean_payload.get("transactionStatusDesc") or clean_payload.get("responseMessage") or transaction.provider_status_text

        now = datetime.utcnow()
        transaction.callback_received_at = now if source == "callback" else transaction.callback_received_at

        # Record timestamps only; callers are responsible for terminal status transitions.
        if status_code == "00":
            transaction.provider_paid_at = transaction.provider_paid_at or now

    def _dana_page_message(self, *, transaction_id: int, amount: float, qr_content: str | None, checkout_url: str | None, expiry_text: str) -> str:
        qr_section = qr_content or "Open the payment page to complete the scan."
        link_line = f"\nPayment link: {checkout_url}" if checkout_url else ""
        return f"""📱 QRIS Payment

💰 Amount: {format_price(amount)}
🆔 Order ID: #{transaction_id}

Scan the QRIS code below to pay the exact amount:
{qr_section}{link_line}

This payment completes automatically after the callback is received.

⏰ Expires: {expiry_text}"""
