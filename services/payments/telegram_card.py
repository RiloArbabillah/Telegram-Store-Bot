"""Telegram native card payment provider."""

from config.settings import settings as app_settings
from database import PaymentMethod, Transaction, TransactionStatus
from utils import format_price

from .base import PaymentCreationError, PaymentPage, PaymentProvider
from .common import complete_transaction, update_transaction_provider_fields


class TelegramCardProvider(PaymentProvider):
    """Provider adapter for Telegram Payments card flow."""

    method = PaymentMethod.CARD
    provider_name = "telegram_payments"
    button_label = "💳 Card"

    def is_available(self) -> bool:
        return bool(app_settings.TELEGRAM_PROVIDER_TOKEN)

    def create_payment(self, session, user, amount: float):
        if not self.is_available():
            raise PaymentCreationError(
                "❌ Card payments are not configured yet.\n\nPlease choose another payment method or contact support."
            )

        transaction = Transaction(
            user_id=user.id,
            amount=amount,
            payment_method=self.method,
            provider_name=self.provider_name,
            status=TransactionStatus.PENDING,
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)

        payload = f"topup_{transaction.id}"
        update_transaction_provider_fields(
            transaction,
            provider_name=self.provider_name,
            external_reference=payload,
            provider_metadata={"invoice_payload": payload},
        )
        session.commit()

        return transaction, PaymentPage(
            message=f"""💳 Card Payment

💰 Amount: {format_price(amount)}
🆔 Order ID: #{transaction.id}

Please complete the secure card payment below 👇""",
            invoice_request={
                "title": "Wallet Top-up",
                "description": f"Add {format_price(amount)} to your wallet balance.",
                "payload": payload,
                "provider_token": app_settings.TELEGRAM_PROVIDER_TOKEN,
                "currency": app_settings.PAYMENT_CURRENCY,
                "prices": [("Wallet Top-up", int(round(amount * 100)))],
                "start_parameter": f"topup-{transaction.id}",
            },
        )

    def validate_precheckout_payload(self, session, payload: str) -> bool:
        transaction = self._get_transaction_by_payload(session, payload)
        return bool(transaction and transaction.status != TransactionStatus.COMPLETED)

    def handle_successful_payment(self, session, payload: str, payment):
        transaction = self._get_transaction_by_payload(session, payload)
        if not transaction:
            return None

        return complete_transaction(
            session,
            transaction,
            provider_name=self.provider_name,
            external_reference=payment.telegram_payment_charge_id,
            provider_metadata={
                "invoice_payload": payload,
                "telegram_payment_charge_id": payment.telegram_payment_charge_id,
                "provider_payment_charge_id": payment.provider_payment_charge_id,
            },
        )

    def _get_transaction_by_payload(self, session, payload: str):
        if not payload.startswith("topup_"):
            return None

        try:
            transaction_id = int(payload.split("_", 1)[1])
        except (ValueError, IndexError):
            return None

        return session.query(Transaction).filter_by(
            id=transaction_id,
            payment_method=self.method,
        ).first()
