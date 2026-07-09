"""Helper utility functions for the Telegram bot."""

import json
from datetime import datetime, timedelta
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from database import get_db_session, User, ProductKey, ProductType

# In-memory cache for ban status (telegram_id: (is_banned, timestamp))
_ban_cache = {}
_BAN_CACHE_TTL = 30  # Cache ban status for 30 seconds


def is_admin(user_id: int) -> bool:
    """Check if a user is an admin based on Telegram ID."""
    return user_id == settings.ADMIN_TELEGRAM_ID


def admin_only(func):
    """Decorator to restrict handler access to admin only."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("⛔ You don't have permission to access this command.")
            return
        return await func(update, context)
    return wrapper


def get_or_create_user(telegram_id: int, username: str = None):
    """Get existing user or create a new one in the database."""
    with get_db_session() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()

        if not user:
            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            session.commit()
            session.refresh(user)

        return user


def format_price(price: int | float) -> str:
    """Format price using IDR whole-rupiah display."""
    normalized = int(round(price or 0))
    return f"Rp{normalized:,}".replace(",", ".")


def format_datetime(dt: datetime) -> str:
    """Format datetime to readable string."""
    return dt.strftime("%b %d, %Y")


def calculate_expiry_time(hours: int = 1) -> datetime:
    """Calculate expiry datetime from now."""
    return datetime.utcnow() + timedelta(hours=hours)


def paginate_items(items, page: int, page_size: int = 5):
    """Paginate a list of items."""
    start = page * page_size
    end = start + page_size
    total_pages = (len(items) + page_size - 1) // page_size

    return {
        'items': items[start:end],
        'page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages - 1,
        'has_prev': page > 0
    }


def validate_amount(amount_str: str) -> tuple[bool, int, str]:
    """Validate user input for IDR whole-rupiah amounts."""
    try:
        normalized_str = amount_str.strip().replace("Rp", "").replace("rp", "").replace(".", "").replace(",", "")
        amount = int(normalized_str)
        if amount <= 0:
            return False, 0, "Amount must be greater than zero."
        if amount > 100000000:
            return False, 0, "Amount is too large. Maximum is Rp100.000.000."
        return True, amount, ""
    except ValueError:
        return False, 0, "Invalid amount. Please enter a whole IDR amount."


def get_effective_product_stock(product, session=None) -> int:
    """Return effective available stock for a product.

    For AKUN products, counts unsold ProductKey rows rather than trusting
    the cached stock_count field.  For all other product types the cached
    stock_count is returned as-is.
    """
    if product.product_type != ProductType.AKUN:
        return int(product.stock_count or 0)

    owns_session = session is None
    if owns_session:
        session = get_db_session()

    try:
        return int(
            session.query(ProductKey)
            .filter_by(product_id=product.id, is_sold=False)
            .count()
        )
    finally:
        if owns_session:
            session.close()


def format_product_display(product, include_description=False, session=None) -> str:
    """Format product information for display."""
    effective_stock = get_effective_product_stock(product, session=session)
    text = f"""📦 Name: {product.name}
💰 Price: {format_price(product.price)}
📦 In Stock: {effective_stock}"""

    if include_description and product.description:
        text += f"\n📝 Description: {product.description}"

    return text


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send notification message to admin."""
    try:
        await context.bot.send_message(
            chat_id=settings.ADMIN_TELEGRAM_ID,
            text=message
        )
    except Exception as e:
        print(f"Error notifying admin: {e}")


def build_availability_text(products_by_category) -> str:
    """Build availability page text with products grouped by category."""
    text = "💬 Our available Products\n\n"

    with get_db_session() as session:
        for category_name, products in products_by_category.items():
            text += f"📦━━━━━{category_name}━━━━━📦\n"
            for product in products:
                effective = get_effective_product_stock(product, session=session)
                text += f"{product.name} | {format_price(product.price)} | Available: {effective}\n"
            text += "\n"

    return text


def parse_keys_from_text(text: str) -> list:
    """Parse keys from text input (one key per line)."""
    keys = [line.strip() for line in text.split('\n') if line.strip()]
    return keys


def parse_supporting_files(raw_value: str | None) -> list[dict]:
    """Parse product supporting files stored as JSON text."""
    if not raw_value:
        return []

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []

    if not isinstance(parsed, list):
        return []

    files = []
    for item in parsed:
        if isinstance(item, dict) and item.get("file_id"):
            files.append({
                "file_id": str(item["file_id"]),
                "file_name": str(item.get("file_name") or "file"),
                "mime_type": str(item.get("mime_type") or ""),
            })

    return files


def dump_supporting_files(files: list[dict] | None) -> str | None:
    """Serialize product supporting file metadata to JSON text."""
    normalized = []
    for item in files or []:
        file_id = item.get("file_id")
        if not file_id:
            continue
        normalized.append({
            "file_id": str(file_id),
            "file_name": str(item.get("file_name") or "file"),
            "mime_type": str(item.get("mime_type") or ""),
        })

    if not normalized:
        return None

    return json.dumps(normalized, ensure_ascii=False)


def check_user_banned(telegram_id: int) -> bool:
    """Check if a user is banned (with caching for performance)."""
    global _ban_cache

    # Check cache first
    if telegram_id in _ban_cache:
        cached_value, cached_time = _ban_cache[telegram_id]
        # If cache is still valid (within TTL), return cached value
        if (datetime.utcnow() - cached_time).total_seconds() < _BAN_CACHE_TTL:
            return cached_value

    # Cache miss or expired - query database
    with get_db_session() as session:
        # Use .scalar() for better performance - only fetch is_banned column
        is_banned = session.query(User.is_banned).filter_by(telegram_id=telegram_id).scalar()
        result = bool(is_banned) if is_banned is not None else False

        # Update cache
        _ban_cache[telegram_id] = (result, datetime.utcnow())

        return result


def clear_ban_cache(telegram_id: int = None):
    """Clear ban cache for a specific user or all users (called when ban status changes)."""
    global _ban_cache
    if telegram_id is None:
        _ban_cache.clear()
    elif telegram_id in _ban_cache:
        del _ban_cache[telegram_id]
