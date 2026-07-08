"""Configuration settings loader from environment variables."""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

PLACEHOLDER_VALUES = {
    '',
    'your_bot_token_here',
    'your_user_id_here',
    'your_username_here',
    'your_api_key_here',
}


def _get_env(name: str, default: str = '') -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return value.strip()


def _get_int_env(name: str, default: int = 0) -> int:
    value = _get_env(name)
    if not value or value in PLACEHOLDER_VALUES:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class Settings:
    """Stores all configuration settings for the bot."""

    # Telegram Bot Settings
    BOT_TOKEN = _get_env('BOT_TOKEN')
    ADMIN_TELEGRAM_ID = _get_int_env('ADMIN_TELEGRAM_ID')
    ADMIN_TELEGRAM_USERNAME = _get_env('ADMIN_TELEGRAM_USERNAME')

    # Database Settings
    DATABASE_URL = _get_env('DATABASE_URL', 'sqlite:///bot_database.db')

    # Crypto Payment Settings
    CRYPTO_BOT_API_KEY = _get_env('CRYPTO_BOT_API_KEY')

    # Telegram Payments (Card) Settings
    # Provider token from @BotFather → your bot → Payments → connect a provider.
    TELEGRAM_PROVIDER_TOKEN = _get_env('TELEGRAM_PROVIDER_TOKEN')
    # Currency the card invoice is charged in. The numeric amount equals the USD
    # top-up value, so this must be a USD-denominated provider for amounts to match.
    PAYMENT_CURRENCY = _get_env('PAYMENT_CURRENCY', 'USD') or 'USD'

    # Application Settings
    PAYMENT_EXPIRY_HOURS = 0.5  # Payment order expiration time (30 minutes)
    PAYMENT_CHECK_INTERVAL = 30  # Seconds between payment verification checks

    # Asset Storage
    ASSETS_DIR = 'assets'
    LOGOS_DIR = os.path.join(ASSETS_DIR, 'logos')
    PRODUCTS_DIR = os.path.join(ASSETS_DIR, 'products')


# Create settings instance
settings = Settings()


def validate_settings():
    """Validates that all required settings are configured."""
    if not settings.BOT_TOKEN or settings.BOT_TOKEN in PLACEHOLDER_VALUES:
        raise ValueError("BOT_TOKEN is required in .env file")

    if not settings.ADMIN_TELEGRAM_ID:
        raise ValueError("ADMIN_TELEGRAM_ID is required in .env file")

    print("[OK] Configuration validated successfully")
