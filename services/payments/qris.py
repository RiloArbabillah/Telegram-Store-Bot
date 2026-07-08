"""Manual QRIS fallback provider until a gateway is selected."""

from config.settings import settings as app_settings
from database import PaymentMethod, Settings, Transaction, TransactionStatus
from utils import calculate_expiry_time, format_price

from .base import PaymentCreationError, PaymentPage, PaymentProvider
from .common import update_transaction_provider_fields


class QrisProvider(PaymentProvider):
    """Manual-review QRIS provider."""

    method = PaymentMethod.QRIS
    provider_name = "qris"
    button_label = "📱 QRIS"

    def is_available(self) -> bool:
        return True

    def create_payment(self, session, user, amount: float):
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
