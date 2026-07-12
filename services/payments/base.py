"""Base types for payment provider integrations."""

from dataclasses import dataclass
from typing import Any

from database import PaymentMethod


class PaymentCreationError(Exception):
    """Raised when a payment request cannot be created."""


@dataclass
class PaymentPage:
    """Normalized payment UI payload returned by providers."""

    message: str
    button_text: str | None = None
    button_url: str | None = None
    invoice_request: dict[str, Any] | None = None
    photo_file_id: str | None = None
    photo_bytes: bytes | None = None
    photo_filename: str | None = None


@dataclass
class PaymentMethodOption:
    """User-facing payment method option."""

    method: PaymentMethod
    label: str
    enabled: bool = True


@dataclass
class PaymentNotification:
    """Notification payload returned after a payment is finalized."""

    user_telegram_id: int
    amount: float
    transaction_id: int
    payment_method: str
    provider_name: str | None = None
    order_id: int | None = None
    order_details: str = ""
    supporting_files: list[dict[str, Any]] | None = None


@dataclass
class PaymentWebhookResult:
    """Result returned by provider webhook processing."""

    handled: bool
    notification: PaymentNotification | None = None


class PaymentProvider:
    """Base class for payment providers."""

    method: PaymentMethod
    provider_name: str
    button_label: str

    def get_option(self) -> PaymentMethodOption:
        return PaymentMethodOption(
            method=self.method,
            label=self.button_label,
            enabled=self.is_available(),
        )

    def is_available(self) -> bool:
        return True

    def create_payment(self, session, user, amount: float) -> tuple[Any, PaymentPage]:
        raise NotImplementedError

    def poll_transaction(self, session, transaction) -> PaymentNotification | None:
        return None

    def validate_precheckout_payload(self, session, payload: str) -> bool:
        return False

    def handle_successful_payment(self, session, payload: str, payment) -> PaymentNotification | None:
        return None

    def process_webhook(self, session, payload: dict[str, Any]) -> PaymentWebhookResult:
        return PaymentWebhookResult(handled=False)
