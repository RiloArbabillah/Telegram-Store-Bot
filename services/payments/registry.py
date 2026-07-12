"""Registry of available payment providers."""

from database import PaymentMethod

from .qris import QrisProvider


_PROVIDERS = [
    QrisProvider(),
]

_PROVIDER_MAP = {provider.method: provider for provider in _PROVIDERS}


def list_payment_providers():
    """Return all registered providers in UI order."""
    return list(_PROVIDERS)


def list_payment_options():
    """Return keyboard-friendly method options."""
    return [provider.get_option() for provider in _PROVIDERS]


def get_provider(payment_method: PaymentMethod):
    """Return the provider for a payment method."""
    return _PROVIDER_MAP[payment_method]


def get_provider_by_callback(callback_data: str):
    """Resolve a provider from callback data in the form pay_<method>."""
    if not callback_data.startswith("pay_"):
        return None

    method_value = callback_data[4:]
    for method in PaymentMethod:
        if method.value == method_value:
            return _PROVIDER_MAP.get(method)

    return None
