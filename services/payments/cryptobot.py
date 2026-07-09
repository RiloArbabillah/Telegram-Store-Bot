"""CryptoBot payment provider."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings as app_settings
from database import PaymentMethod, Transaction, TransactionStatus
from services.crypto_bot import CryptoBotService
from utils import calculate_expiry_time, format_price

from .base import PaymentCreationError, PaymentPage, PaymentProvider, PaymentWebhookResult
from .common import complete_transaction, extract_checkout_url, extract_external_reference, hydrate_legacy_transaction, update_transaction_provider_fields


class CryptoBotProvider(PaymentProvider):
    """Provider adapter for CryptoBot invoices."""

    method = PaymentMethod.CRYPTO_WALLET
    provider_name = "cryptobot"
    button_label = "🪙 CryptoBot"

    def is_available(self) -> bool:
        return bool(app_settings.CRYPTO_BOT_API_KEY) and app_settings.PAYMENT_CURRENCY == "USD"

    def create_payment(self, session, user, amount: float):
        if not self.is_available():
            raise PaymentCreationError(
                "❌ CryptoBot top-up is disabled for the current IDR wallet setup.\n\nPlease choose QRIS instead."
            )

        existing_pending = session.query(Transaction).filter_by(
            user_id=user.id,
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).first()

        if existing_pending:
            hydrate_legacy_transaction(existing_pending)
            return existing_pending, self._build_payment_page(existing_pending, is_existing=True)

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

        payment_reference = CryptoBotService().generate_payment_address(amount, transaction.id)
        if not payment_reference:
            transaction.status = TransactionStatus.FAILED
            session.commit()
            raise PaymentCreationError("❌ Failed to generate payment invoice. Please try again.")

        invoice_id, pay_url = self._parse_payment_reference(payment_reference)
        update_transaction_provider_fields(
            transaction,
            provider_name=self.provider_name,
            external_reference=invoice_id,
            checkout_url=pay_url,
            provider_metadata={"invoice_id": invoice_id, "pay_url": pay_url},
            legacy_reference=payment_reference,
        )
        session.commit()

        return transaction, self._build_payment_page(transaction)

    def poll_transaction(self, session, transaction):
        hydrate_legacy_transaction(transaction)
        reference = transaction.external_reference or transaction.crypto_address
        if not reference:
            return None

        is_paid = CryptoBotService().check_payment_status(reference, transaction.amount)
        if not is_paid:
            return None

        return complete_transaction(
            session,
            transaction,
            provider_name=self.provider_name,
            external_reference=extract_external_reference(transaction),
            checkout_url=extract_checkout_url(transaction),
        )

    def process_webhook(self, session, payload: dict):
        invoice_id = payload.get("invoice_id")
        status = payload.get("status")

        if status != "paid" or not invoice_id:
            return PaymentWebhookResult(handled=False)

        transactions = session.query(Transaction).filter_by(
            payment_method=self.method,
            status=TransactionStatus.PENDING,
        ).all()

        for transaction in transactions:
            hydrate_legacy_transaction(transaction)
            if str(transaction.external_reference or "") != str(invoice_id):
                continue

            notification = complete_transaction(
                session,
                transaction,
                provider_name=self.provider_name,
                external_reference=str(invoice_id),
                checkout_url=extract_checkout_url(transaction),
                provider_metadata=payload,
            )
            return PaymentWebhookResult(handled=True, notification=notification)

        return PaymentWebhookResult(handled=False)

    def _parse_payment_reference(self, payment_reference: str) -> tuple[str, str]:
        if "|" in payment_reference:
            invoice_id, pay_url = payment_reference.split("|", 1)
            return invoice_id, pay_url
        return payment_reference, payment_reference

    def _build_payment_page(self, transaction, *, is_existing: bool = False) -> PaymentPage:
        pay_url = extract_checkout_url(transaction) or "#"
        prefix = "⚠️ You already have a pending CryptoBot payment!\n\n" if is_existing else ""
        expiry_text = transaction.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC') if transaction.expires_at else 'N/A'

        message = f"""{prefix}💬 CryptoBot Payment

💰 Amount: {format_price(transaction.amount)}
🆔 Order ID: #{transaction.id}

Click the button below to open the payment page. You can pay with ANY cryptocurrency supported by CryptoBot:

✅ BTC (Bitcoin)
✅ TON (Toncoin)
✅ USDT (TRC20, TON)
✅ USDC (TRC20, TON)
✅ ETH (Ethereum)
✅ LTC (Litecoin)
✅ BNB (Binance Coin)
✅ TRX (Tron)
And many more!

The system will automatically verify and add {format_price(transaction.amount)} to your balance as soon as your payment is confirmed.

⏰ Expires: {expiry_text}"""

        return PaymentPage(
            message=message,
            button_text="💳 Pay with Any Crypto",
            button_url=pay_url,
        )
