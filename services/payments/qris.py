"""QRIS provider placeholder until a gateway is selected."""

from database import PaymentMethod

from .base import PaymentCreationError, PaymentProvider


class QrisProvider(PaymentProvider):
    """Provider slot for a future QRIS gateway integration."""

    method = PaymentMethod.QRIS
    provider_name = "qris"
    button_label = "📱 QRIS"

    def is_available(self) -> bool:
        return True

    def create_payment(self, session, user, amount: float):
        raise PaymentCreationError(
            "❌ QRIS is not configured yet.\n\nChoose another payment method for now."
        )
