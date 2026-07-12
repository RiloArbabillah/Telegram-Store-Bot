"""Payment and direct checkout handlers."""

from io import BytesIO
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import (
    get_db_session, User, Transaction, Product,
    ProductKey, TransactionStatus, PaymentMethod, ProductType
)
from utils import (
    format_price,
    create_quantity_keyboard, create_main_menu_keyboard,
    notify_admin, check_user_banned, parse_supporting_files
)
from services.payments import (
    PaymentCreationError,
    get_provider,
    hydrate_legacy_transaction,
)
from services.payments.common import (
    get_qris_message_refs,
    is_manual_qris_expired,
    parse_provider_metadata,
    register_qris_message_ref,
)
from services.payments.qris_messages import cleanup_qris_messages
from services.direct_checkout import CheckoutError, create_pending_checkout, expire_pending_checkout
from config.settings import settings as app_settings

# Conversation states for direct purchase
PURCHASE_QUANTITY = 10


def _is_photo_supporting_file(file_info: dict) -> bool:
    file_type = str(file_info.get("file_type") or "").lower()
    mime_type = str(file_info.get("mime_type") or "").lower()
    file_name = str(file_info.get("file_name") or "").lower()
    return file_type == "photo" or mime_type.startswith("image/") or file_name == "photo"


async def send_supporting_file(bot, chat_id: int, file_info: dict) -> bool:
    """Send a supporting file using the Telegram method matching its media type."""
    caption = file_info.get("caption") or file_info.get("file_name") or "Supporting file"
    source = file_info.get("storage_path") or file_info.get("file_id")
    try:
        if file_info.get("storage_path"):
            with open(source, "rb") as local_file:
                if _is_photo_supporting_file(file_info):
                    await bot.send_photo(chat_id=chat_id, photo=local_file, caption=caption)
                else:
                    await bot.send_document(chat_id=chat_id, document=local_file, caption=caption)
        elif _is_photo_supporting_file(file_info):
            await bot.send_photo(chat_id=chat_id, photo=source, caption=caption)
        else:
            await bot.send_document(chat_id=chat_id, document=source, caption=caption)
        return True
    except Exception as exc:
        print(f"❌ Failed to send supporting file {file_info.get('file_name', 'file')}: {exc}")
        return False


def _payment_page_markup(payment_page):
    """Build inline keyboard markup for a payment page."""
    if payment_page.button_text and payment_page.button_url:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(payment_page.button_text, url=payment_page.button_url)],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ])

    return None


async def _send_payment_page(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_page):
    """Render a provider payment page, optionally with media."""
    query = update.callback_query

    if payment_page.photo_bytes:
        await query.edit_message_text(
            "✅ Payment instructions sent below. Complete the payment there and follow the next step in chat."
        )
        photo = BytesIO(payment_page.photo_bytes)
        photo.name = payment_page.photo_filename or "qris.png"
        return await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=payment_page.message,
            reply_markup=_payment_page_markup(payment_page),
        )

    if payment_page.photo_file_id:
        await query.edit_message_text(
            "✅ Payment instructions sent below. Complete the payment there and follow the next step in chat."
        )
        return await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=payment_page.photo_file_id,
            caption=payment_page.message,
            reply_markup=_payment_page_markup(payment_page),
        )

    edited_message = await query.edit_message_text(
        payment_page.message,
        reply_markup=_payment_page_markup(payment_page),
    )
    return edited_message if hasattr(edited_message, "message_id") else query.message


def _build_payment_notifications(notif):
    """Build user/admin confirmation messages for a completed payment."""
    order_line = f"\n📝 Order ID: #{notif.order_id}" if notif.order_id else ""
    details = f"\n\n{notif.order_details}" if notif.order_details else ""
    user_message = f"""✅ Payment Confirmed!

💳 Method: {notif.payment_method}
💰 Amount: {format_price(notif.amount)}{order_line}
{details}

Thank you for your payment!"""

    admin_message = f"""💰 New Payment Received

👤 User ID: {notif.user_telegram_id}
💰 Amount: {format_price(notif.amount)}
📝 Transaction ID: #{notif.transaction_id}
{f"🛍 Order ID: #{notif.order_id}" if notif.order_id else ""}
🔄 Payment Method: {notif.payment_method}"""

    return user_message, admin_message


async def qris_proof_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user-submitted proof for a pending manual QRIS payment."""
    pending_transaction_id = context.user_data.get('pending_qris_transaction_id')

    if not update.message or not (update.message.photo or update.message.document):
        return

    with get_db_session() as session:
        transaction = None
        if pending_transaction_id:
            transaction = session.query(Transaction).filter_by(id=pending_transaction_id).first()

        if not transaction:
            user = session.query(User).filter_by(telegram_id=update.effective_user.id).first()
            if not user:
                return

            transaction = (
                session.query(Transaction)
                .filter_by(
                    user_id=user.id,
                    payment_method=PaymentMethod.QRIS,
                    status=TransactionStatus.PENDING,
                )
                .filter((Transaction.provider_name == None) | (Transaction.provider_name != 'dana_qris'))
                .order_by(Transaction.created_at.desc())
                .first()
            )

        if not transaction:
            return

        if transaction.payment_method != PaymentMethod.QRIS or transaction.provider_name == 'dana_qris':
            return

        if transaction.status != TransactionStatus.PENDING:
            await update.message.reply_text("ℹ️ This QRIS order is no longer awaiting proof.")
            return

        if is_manual_qris_expired(transaction):
            if transaction.order_id:
                expire_pending_checkout(session, transaction.id)
            else:
                transaction.status = TransactionStatus.EXPIRED
            session.commit()
            expired_transaction_id = transaction.id
            context.user_data.pop('pending_qris_transaction_id', None)
            await cleanup_qris_messages(context.bot, expired_transaction_id)
            await update.message.reply_text(
                "⏰ This QRIS order has expired. Please create a new checkout.",
                reply_markup=create_main_menu_keyboard()
            )
            return

        user = session.query(User).filter_by(id=transaction.user_id).first()
        if not user or user.telegram_id != update.effective_user.id:
            return

        if update.message.photo:
            proof_file_id = update.message.photo[-1].file_id
            proof_file_type = 'photo'
        else:
            proof_file_id = update.message.document.file_id
            proof_file_type = 'document'

        transaction.proof_file_id = proof_file_id
        transaction.proof_file_type = proof_file_type
        transaction.proof_submitted_at = datetime.utcnow()
        session.commit()

        username = update.effective_user.username or f"ID:{update.effective_user.id}"
        amount = transaction.amount
        metadata = parse_provider_metadata(transaction.provider_metadata)
        payable_amount = int(metadata.get("payable_amount") or amount)
        unique_code = int(metadata.get("unique_code") or 0)
        transaction_id = transaction.id

    context.user_data.pop('pending_qris_transaction_id', None)

    await update.message.reply_text(
        "✅ Payment proof received. Admin will review it shortly.",
        reply_markup=create_main_menu_keyboard()
    )

    admin_caption = (
        f"📱 QRIS Proof Submitted\n\n"
        f"🆔 Transaction: #{transaction_id}\n"
        f"👤 User: @{username}\n"
        f"💰 Requested Amount: {format_price(amount)}\n\n"
        f"🔢 Unique Code: {unique_code:03d}\n"
        f"✅ Expected Paid: {format_price(payable_amount)}\n\n"
        f"Review the proof and choose an action:"
    )
    admin_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Default", callback_data=f"confirm_payment_{transaction_id}")],
        [InlineKeyboardButton("✏️ Input Nominal", callback_data=f"input_payment_nominal_{transaction_id}")],
        [InlineKeyboardButton("❌ Reject Payment", callback_data=f"cancel_payment_{transaction_id}")],
    ])

    try:
        if proof_file_type == 'photo':
            await context.bot.send_photo(
                chat_id=app_settings.ADMIN_TELEGRAM_ID,
                photo=proof_file_id,
                caption=admin_caption,
                reply_markup=admin_keyboard,
            )
        else:
            await context.bot.send_document(
                chat_id=app_settings.ADMIN_TELEGRAM_ID,
                document=proof_file_id,
                caption=admin_caption,
                reply_markup=admin_keyboard,
            )
    except Exception:
        pass


async def cancel_payment_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel from payment instruction page (outside conversation)."""
    query = update.callback_query
    await query.answer()

    from utils import create_main_menu_keyboard

    cleanup_transaction_id = None
    pending_transaction_id = context.user_data.get('pending_qris_transaction_id')
    if pending_transaction_id:
        with get_db_session() as session:
            transaction = session.query(Transaction).filter_by(id=pending_transaction_id).first()
            if (
                transaction
                and transaction.payment_method == PaymentMethod.QRIS
                and transaction.status == TransactionStatus.PENDING
            ):
                if transaction.order_id:
                    expire_pending_checkout(session, transaction.id)
                else:
                    transaction.status = TransactionStatus.FAILED
                cleanup_transaction_id = transaction.id
                session.commit()

    # Clear user data
    context.user_data.clear()

    if cleanup_transaction_id:
        await cleanup_qris_messages(context.bot, cleanup_transaction_id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Payment cancelled. You can try again anytime.",
            reply_markup=create_main_menu_keyboard(),
        )
    else:
        await query.edit_message_text(
            "❌ Payment cancelled. You can try again anytime.",
            reply_markup=create_main_menu_keyboard(),
        )


async def check_pending_payments(context: ContextTypes.DEFAULT_TYPE):
    """Background job to check pending payment transactions (non-blocking)."""
    import asyncio

    def _check_and_process_payments_sync():
        """Synchronous database operations run in thread pool."""
        payment_notifications = []

        with get_db_session() as session:
            pending_transactions = session.query(Transaction).filter_by(
                status=TransactionStatus.PENDING
            ).all()

            for transaction in pending_transactions:
                hydrate_legacy_transaction(transaction)

                # Check if transaction has expired
                if transaction.expires_at and datetime.utcnow() > transaction.expires_at:
                    continue  # Will be handled by check_expired_payments

                provider = get_provider(transaction.payment_method)
                notification = provider.poll_transaction(session, transaction)
                if notification:
                    payment_notifications.append(notification)

        return payment_notifications

    # Run blocking database operations in thread pool
    notifications = await asyncio.to_thread(_check_and_process_payments_sync)

    # Send notifications asynchronously
    for notif in notifications:
        await cleanup_qris_messages(context.bot, notif.transaction_id)
        user_message, admin_message = _build_payment_notifications(notif)

        try:
            await context.bot.send_message(
                chat_id=notif.user_telegram_id,
                text=user_message
            )
            for file_info in notif.supporting_files or []:
                await send_supporting_file(context.bot, notif.user_telegram_id, file_info)
        except Exception:
            pass

        await notify_admin(context, admin_message)


async def check_expired_payments(context: ContextTypes.DEFAULT_TYPE):
    """Background job to mark expired payment transactions (non-blocking)."""
    import asyncio

    def _check_expired_sync():
        """Synchronous database operations run in thread pool."""
        expired_notifications = []

        with get_db_session() as session:
            pending_transactions = session.query(Transaction).filter_by(
                status=TransactionStatus.PENDING
            ).all()

            for transaction in pending_transactions:
                if transaction.expires_at and datetime.utcnow() > transaction.expires_at:
                    # Mark as expired
                    if transaction.order_id:
                        expire_pending_checkout(session, transaction.id)
                    else:
                        transaction.status = TransactionStatus.EXPIRED

                    # Get user info for notification
                    user = session.query(User).filter_by(id=transaction.user_id).first()
                    if user:
                        expired_notifications.append({
                            'telegram_id': user.telegram_id,
                            'amount': transaction.amount,
                            'transaction_id': transaction.id
                        })

                    session.commit()

            cleanup_transaction_ids = [
                transaction.id
                for transaction in session.query(Transaction).filter(
                    Transaction.payment_method == PaymentMethod.QRIS,
                    Transaction.status.in_([
                        TransactionStatus.COMPLETED,
                        TransactionStatus.EXPIRED,
                        TransactionStatus.FAILED,
                    ]),
                ).all()
                if get_qris_message_refs(transaction)
            ]

        return expired_notifications, cleanup_transaction_ids

    # Run blocking database operations in thread pool
    notifications, cleanup_transaction_ids = await asyncio.to_thread(_check_expired_sync)

    for transaction_id in cleanup_transaction_ids:
        await cleanup_qris_messages(context.bot, transaction_id)

    # Send notifications asynchronously
    for notif in notifications:
        message = f"""⏰ Payment Order Expired

💰 Amount: {format_price(notif['amount'])}
📝 Transaction ID: #{notif['transaction_id']}

Your payment order has expired. Please create a new checkout if you still want to buy this product."""

        try:
            await context.bot.send_message(
                chat_id=notif['telegram_id'],
                text=message
            )
        except Exception:
            # User may have blocked the bot
            pass


async def buy_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the direct purchase flow - ask for quantity."""
    query = update.callback_query
    await query.answer()

    # Check if user is banned
    if check_user_banned(update.effective_user.id):
        await query.edit_message_text("⛔ You have been banned from using this bot.")
        return ConversationHandler.END

    # Extract product_id from callback data (format: buy_123)
    product_id = int(query.data.split("_")[1])

    with get_db_session() as session:
        product = session.query(Product).filter_by(id=product_id).first()

        if not product:
            await query.edit_message_text("❌ Product not found.")
            return ConversationHandler.END

        if not product.is_active:
            await query.edit_message_text("❌ This product is no longer available.")
            return ConversationHandler.END

        # Use effective stock for AKUN (unsold ProductKey rows), cached stock_count otherwise
        from utils.helpers import get_effective_product_stock
        effective_stock = get_effective_product_stock(product, session=session)

        if effective_stock == 0:
            await query.edit_message_text("❌ This product is out of stock.")
            return ConversationHandler.END

        # Store product info in context for later
        context.user_data['purchase_product_id'] = product_id
        context.user_data['purchase_product_name'] = product.name
        context.user_data['purchase_product_price'] = product.price
        context.user_data['purchase_product_stock'] = effective_stock
        context.user_data['purchase_product_type'] = product.product_type

        # For file products, quantity is always 1
        if product.product_type == ProductType.FILE:
            context.user_data['purchase_quantity'] = 1
            # Skip quantity input, go straight to confirmation
            return await show_purchase_confirmation(update, context)

        # For key/akun products, ask for quantity
        message = f"""🛒 Purchase: {product.name}

💰 Price: {format_price(product.price)} each
📦 Available: {effective_stock}

💬 Please enter the quantity you want to buy (1-{effective_stock}):"""

        # If coming from a photo message, delete it and create new text message
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(
                message,
                reply_markup=create_quantity_keyboard(product_id)
            )
        else:
            await query.edit_message_text(
                message,
                reply_markup=create_quantity_keyboard(product_id)
            )

        return PURCHASE_QUANTITY


async def purchase_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quantity input for direct purchase."""
    quantity_str = update.message.text.strip()

    # Validate quantity
    try:
        quantity = int(quantity_str)
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid number.",
            reply_markup=create_quantity_keyboard(context.user_data.get('purchase_product_id', 0))
        )
        return PURCHASE_QUANTITY

    product_stock = context.user_data.get('purchase_product_stock', 0)

    if quantity < 1:
        await update.message.reply_text(
            "❌ Quantity must be at least 1.",
            reply_markup=create_quantity_keyboard(context.user_data.get('purchase_product_id', 0))
        )
        return PURCHASE_QUANTITY

    if quantity > product_stock:
        await update.message.reply_text(
            f"❌ Not enough stock. Maximum available: {product_stock}",
            reply_markup=create_quantity_keyboard(context.user_data.get('purchase_product_id', 0))
        )
        return PURCHASE_QUANTITY

    # Store quantity and show confirmation
    context.user_data['purchase_quantity'] = quantity
    return await show_purchase_confirmation(update, context, is_message=True)


async def show_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    """Show purchase confirmation with total price."""
    product_id = context.user_data.get('purchase_product_id')
    product_name = context.user_data.get('purchase_product_name')
    product_price = context.user_data.get('purchase_product_price')
    quantity = context.user_data.get('purchase_quantity')

    total = product_price * quantity
    message = f"""🛒 Confirm Purchase

📦 Product: {product_name}
💰 Price: {format_price(product_price)} x {quantity}
💵 Total: {format_price(total)}"""

    keyboard = [
        [InlineKeyboardButton("📱 Bayar QRIS", callback_data=f"confirm_purchase_{product_id}_{quantity}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_purchase")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if is_message:
        await update.message.reply_text(message, reply_markup=reply_markup)
    else:
        query = update.callback_query
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(message, reply_markup=reply_markup)
        else:
            await query.edit_message_text(message, reply_markup=reply_markup)

    return ConversationHandler.END


async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a pending order and show QRIS payment instructions."""
    query = update.callback_query
    await query.answer()

    # Check if user is banned
    if check_user_banned(update.effective_user.id):
        await query.edit_message_text("⛔ You have been banned from using this bot.")
        return

    # Extract product_id and quantity from callback data (format: confirm_purchase_123_5)
    parts = query.data.split("_")
    product_id = int(parts[2])
    quantity = int(parts[3])

    telegram_id = update.effective_user.id

    with get_db_session() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            await query.edit_message_text("❌ User not found.")
            return

        product = session.query(Product).filter_by(id=product_id).first()
        if not product:
            await query.edit_message_text("❌ Product not found.")
            return

        if not product.is_active:
            await query.edit_message_text("❌ This product is no longer available.")
            return

        from utils.helpers import get_effective_product_stock
        effective_stock = get_effective_product_stock(product, session=session)
        if effective_stock < quantity:
            await query.edit_message_text(
                f"❌ Not enough stock. Only {effective_stock} available.",
                reply_markup=create_main_menu_keyboard()
            )
            return

        total = product.price * quantity

        try:
            order = create_pending_checkout(session, user_id=user.id, product_id=product.id, quantity=quantity)
            provider = get_provider(PaymentMethod.QRIS)
            transaction, payment_page = provider.create_payment(session, user, order.total_amount, order_id=order.id)
        except (CheckoutError, PaymentCreationError) as exc:
            session.rollback()
            await query.edit_message_text(str(exc), reply_markup=create_main_menu_keyboard())
            return

        payment_message = await _send_payment_page(update, context, payment_page)
        if payment_message:
            session.refresh(transaction)
            register_qris_message_ref(
                transaction,
                chat_id=payment_message.chat_id,
                message_id=payment_message.message_id,
            )
            context.user_data['pending_qris_transaction_id'] = transaction.id
            session.commit()


async def cancel_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the purchase process."""
    query = update.callback_query
    await query.answer()

    from utils import create_main_menu_keyboard

    # Clear purchase data
    context.user_data.pop('purchase_product_id', None)
    context.user_data.pop('purchase_product_name', None)
    context.user_data.pop('purchase_product_price', None)
    context.user_data.pop('purchase_product_stock', None)
    context.user_data.pop('purchase_product_type', None)
    context.user_data.pop('purchase_quantity', None)

    await query.edit_message_text(
        "❌ Purchase cancelled.",
        reply_markup=create_main_menu_keyboard()
    )

    return ConversationHandler.END


def assign_product_keys(session, product_id: int, quantity: int, order_id: int) -> list:
    """Atomically assign product keys to an order from the product_keys table."""
    # Get available keys (not sold)
    available_keys = session.query(ProductKey).filter_by(
        product_id=product_id,
        is_sold=False
    ).order_by(ProductKey.id.asc()).limit(quantity).with_for_update().all()

    if len(available_keys) < quantity:
        raise ValueError(f"Not enough keys available. Requested: {quantity}, Available: {len(available_keys)}")

    assigned_keys = []
    for key in available_keys:
        key.is_sold = True
        key.order_id = order_id
        key.sold_at = datetime.utcnow()
        assigned_keys.append({
            "key_value": key.key_value,
            "supporting_files": parse_supporting_files(key.supporting_files),
        })

    return assigned_keys


async def broadcast_availability_to_all_users(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job to broadcast availability to all users every 12 hours (non-blocking with rate limiting)."""
    import asyncio
    import logging
    from utils import build_availability_text

    logger = logging.getLogger(__name__)
    logger.info("Starting availability broadcast to all users...")

    def _get_users_and_availability_sync():
        """Synchronous database operations run in thread pool."""
        try:
            with get_db_session() as session:
                from database import Category, Product

                # Get all non-banned users
                users = session.query(User).filter_by(is_banned=False).all()
                user_ids = [user.telegram_id for user in users]

                logger.info(f"Found {len(user_ids)} users to notify")

                # Build products by category dictionary
                products_by_category = {}
                categories = session.query(Category).all()

                for category in categories:
                    products = session.query(Product).filter_by(
                        category_id=category.id,
                        is_active=True
                    ).limit(15).all()

                    if products:
                        products_by_category[category.name] = products

                # Get availability text
                if not products_by_category:
                    availability_text = "📦 No products available yet."
                else:
                    availability_text = build_availability_text(products_by_category)

                return user_ids, availability_text
        except Exception as e:
            logger.error(f"Error in _get_users_and_availability_sync: {e}")
            raise

    try:
        # Run blocking database operations in thread pool
        user_ids, availability_text = await asyncio.to_thread(_get_users_and_availability_sync)
    except Exception as e:
        logger.error(f"Failed to get users and availability: {e}")
        return

    if not user_ids:
        logger.info("No users to notify, skipping broadcast")
        return  # No users to notify

    logger.info(f"Broadcasting availability to {len(user_ids)} users...")

    # Create availability keyboard
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [InlineKeyboardButton("🛒 Browse Products", callback_data="products")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send to all users with rate limiting
    success_count = 0
    fail_count = 0

    for telegram_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=availability_text,
                reply_markup=reply_markup
            )
            success_count += 1

            # Rate limiting: 50ms delay = ~20 messages/second (well under Telegram's 30/sec limit)
            await asyncio.sleep(0.05)
        except Exception as e:
            # User may have blocked the bot
            logger.debug(f"Failed to send to {telegram_id}: {e}")
            fail_count += 1

    logger.info(f"Availability broadcast complete: {success_count} sent, {fail_count} failed")

    # Notify admin about broadcast completion
    try:
        from utils import notify_admin
        admin_message = f"""📢 Availability Broadcast Complete

✅ Sent successfully: {success_count}
❌ Failed: {fail_count}
👥 Total users: {len(user_ids)}"""

        await notify_admin(context, admin_message)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
