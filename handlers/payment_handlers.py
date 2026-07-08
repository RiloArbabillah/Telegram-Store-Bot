"""Payment and wallet management handlers."""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes, ConversationHandler
from database import (
    get_db_session, User, Transaction, Order, OrderItem, Product,
    ProductKey, TransactionStatus, OrderStatus, PaymentMethod, ProductType
)
from utils import (
    get_or_create_user, format_price, validate_amount,
    create_cancel_keyboard, create_payment_method_keyboard,
    create_quantity_keyboard, create_main_menu_keyboard,
    notify_admin, check_user_banned
)
from services.payments import (
    PaymentCreationError,
    get_provider,
    get_provider_by_callback,
    hydrate_legacy_transaction,
    list_payment_options,
    list_payment_providers,
)

# Conversation states for top-up
AMOUNT, METHOD = range(2)

# Conversation states for direct purchase
PURCHASE_QUANTITY = 10


async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the wallet top-up flow."""
    query = update.callback_query
    await query.answer()

    message = "💬 Please reply the amount USD you want to fund your wallet.\nExample: 100"

    await query.edit_message_text(
        message,
        reply_markup=create_cancel_keyboard()
    )

    return AMOUNT


async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle amount input for wallet top-up."""
    amount_str = update.message.text

    # Validate amount
    is_valid, amount, error_msg = validate_amount(amount_str)

    if not is_valid:
        await update.message.reply_text(
            f"❌ {error_msg}\n\nPlease enter a valid amount:",
            reply_markup=create_cancel_keyboard()
        )
        return AMOUNT

    # Store amount in context
    context.user_data['topup_amount'] = amount

    payment_options = [
        (option.label, f"pay_{option.method.value}")
        for option in list_payment_options()
    ]

    message = f"💰 Amount: ${amount:.2f}\n\n💬 Please choose a payment method:"

    await update.message.reply_text(
        message,
        reply_markup=create_payment_method_keyboard(payment_options)
    )

    return METHOD


def _payment_page_markup(payment_page):
    """Build inline keyboard markup for a payment page."""
    if payment_page.button_text and payment_page.button_url:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(payment_page.button_text, url=payment_page.button_url)],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ])

    return None


def _build_payment_notifications(notif):
    """Build user/admin confirmation messages for a completed payment."""
    user_message = f"""✅ Payment Confirmed!

💳 Method: {notif.payment_method}
💰 Amount: {format_price(notif.amount)}
🔄 Your new wallet balance: {format_price(notif.new_balance)}

Thank you for your payment!"""

    admin_message = f"""💰 New Payment Received

👤 User ID: {notif.user_telegram_id}
💰 Amount: {format_price(notif.amount)}
📝 Transaction ID: #{notif.transaction_id}
🔄 Payment Method: {notif.payment_method}"""

    return user_message, admin_message


async def payment_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle provider-driven payment method selection."""
    query = update.callback_query
    await query.answer()

    provider = get_provider_by_callback(query.data)
    if not provider:
        await query.edit_message_text("❌ Unknown payment method. Please start again.")
        return ConversationHandler.END

    usd_amount = context.user_data.get('topup_amount', 0)
    user_id = update.effective_user.id

    if usd_amount <= 0:
        await query.edit_message_text("❌ Invalid amount. Please start the top-up again.")
        return ConversationHandler.END

    with get_db_session() as session:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            await query.edit_message_text("❌ User not found.")
            return ConversationHandler.END

        try:
            _, payment_page = provider.create_payment(session, user, usd_amount)
        except PaymentCreationError as exc:
            payment_options = [
                (option.label, f"pay_{option.method.value}")
                for option in list_payment_options()
            ]
            await query.edit_message_text(
                str(exc),
                reply_markup=create_payment_method_keyboard(payment_options),
            )
            return METHOD

        await query.edit_message_text(
            payment_page.message,
            reply_markup=_payment_page_markup(payment_page),
        )

        if payment_page.invoice_request:
            prices = [
                LabeledPrice(label=label, amount=minor_units)
                for label, minor_units in payment_page.invoice_request["prices"]
            ]
            await context.bot.send_invoice(
                chat_id=update.effective_chat.id,
                title=payment_page.invoice_request["title"],
                description=payment_page.invoice_request["description"],
                payload=payment_page.invoice_request["payload"],
                provider_token=payment_page.invoice_request["provider_token"],
                currency=payment_page.invoice_request["currency"],
                prices=prices,
                start_parameter=payment_page.invoice_request["start_parameter"],
            )

    return ConversationHandler.END


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve the pre-checkout query if it maps to a valid pending payment."""
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""

    is_valid = False
    with get_db_session() as session:
        for provider in list_payment_providers():
            if provider.validate_precheckout_payload(session, payload):
                is_valid = True
                break

    if is_valid:
        await query.answer(ok=True)
    else:
        await query.answer(
            ok=False,
            error_message="This payment order is no longer valid. Please start a new top-up."
        )


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Credit the wallet once Telegram confirms a successful payment."""
    payment = update.message.successful_payment
    payload = payment.invoice_payload or ""

    notif = None
    with get_db_session() as session:
        for provider in list_payment_providers():
            notif = provider.handle_successful_payment(session, payload, payment)
            if notif:
                break

    if not notif:
        return

    user_message, admin_message = _build_payment_notifications(notif)
    await update.message.reply_text(user_message, reply_markup=create_main_menu_keyboard())
    await notify_admin(context, admin_message)


async def cancel_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the top-up process (during conversation)."""
    query = update.callback_query
    await query.answer()

    from utils import create_back_support_keyboard

    await query.edit_message_text(
        "❌ Top-up cancelled.",
        reply_markup=create_back_support_keyboard()
    )

    # Clear user data
    context.user_data.clear()

    return ConversationHandler.END


async def cancel_payment_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel from payment instruction page (outside conversation)."""
    query = update.callback_query
    await query.answer()

    from utils import create_main_menu_keyboard

    await query.edit_message_text(
        "❌ Payment cancelled. You can try again anytime.",
        reply_markup=create_main_menu_keyboard()
    )

    # Clear user data
    context.user_data.clear()


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
        user_message, admin_message = _build_payment_notifications(notif)

        try:
            await context.bot.send_message(
                chat_id=notif.user_telegram_id,
                text=user_message
            )
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

        return expired_notifications

    # Run blocking database operations in thread pool
    notifications = await asyncio.to_thread(_check_expired_sync)

    # Send notifications asynchronously
    for notif in notifications:
        message = f"""⏰ Payment Order Expired

💰 Amount: {format_price(notif['amount'])}
📝 Transaction ID: #{notif['transaction_id']}

Your payment order has expired. Please create a new top-up request if you still want to fund your wallet."""

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

        if product.stock_count == 0:
            await query.edit_message_text("❌ This product is out of stock.")
            return ConversationHandler.END

        # Store product info in context for later
        context.user_data['purchase_product_id'] = product_id
        context.user_data['purchase_product_name'] = product.name
        context.user_data['purchase_product_price'] = product.price
        context.user_data['purchase_product_stock'] = product.stock_count
        context.user_data['purchase_product_type'] = product.product_type

        # For file products, quantity is always 1
        if product.product_type == ProductType.FILE:
            context.user_data['purchase_quantity'] = 1
            # Skip quantity input, go straight to confirmation
            return await show_purchase_confirmation(update, context)

        # For key products, ask for quantity
        message = f"""🛒 Purchase: {product.name}

💰 Price: {format_price(product.price)} each
📦 Available: {product.stock_count}

💬 Please enter the quantity you want to buy (1-{product.stock_count}):"""

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
    telegram_id = update.effective_user.id

    with get_db_session() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            if is_message:
                await update.message.reply_text("❌ User not found.")
            else:
                await update.callback_query.edit_message_text("❌ User not found.")
            return ConversationHandler.END

        wallet_balance = user.wallet_balance
        has_sufficient_balance = wallet_balance >= total

        if has_sufficient_balance:
            balance_text = f"💰 Your Wallet Balance: {format_price(wallet_balance)}"
        else:
            balance_text = f"⚠️ Insufficient Balance!\n💰 Your Wallet Balance: {format_price(wallet_balance)}\n\n💡 Please top up your wallet first."

        message = f"""🛒 Confirm Purchase

📦 Product: {product_name}
💰 Price: {format_price(product_price)} x {quantity}
💵 Total: {format_price(total)}

{balance_text}"""

        if has_sufficient_balance:
            keyboard = [
                [InlineKeyboardButton("✅ Confirm Purchase", callback_data=f"confirm_purchase_{product_id}_{quantity}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_purchase")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("💰 Top Up Wallet", callback_data="topup")],
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
    """Process the confirmed purchase."""
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

        if product.stock_count < quantity:
            await query.edit_message_text(f"❌ Not enough stock. Only {product.stock_count} available.")
            return

        total = product.price * quantity

        # Check balance
        if user.wallet_balance < total:
            await query.edit_message_text(
                f"❌ Insufficient balance.\n💰 Your balance: {format_price(user.wallet_balance)}\n💵 Required: {format_price(total)}"
            )
            return

        # Create order
        order = Order(
            user_id=user.id,
            total_amount=total,
            status=OrderStatus.COMPLETED
        )
        session.add(order)
        session.commit()
        session.refresh(order)

        # Create order item
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=quantity,
            price=product.price
        )

        # Deliver assets based on product type
        order_details = ""
        if product.product_type == ProductType.KEY:
            # Atomically assign keys from product_keys table
            keys = assign_product_keys(session, product.id, quantity, order.id)
            order_item.delivered_asset = "\n".join(keys)
            order_details = f"📦 {product.name} (x{quantity})\n🔐 Keys:\n{order_item.delivered_asset}\n"

        elif product.product_type == ProductType.FILE:
            # Provide download link
            order_item.delivered_asset = product.download_link
            order_details = f"📦 {product.name}\n🔗 Download: {order_item.delivered_asset}\n"

        # Update product stock
        product.stock_count -= quantity

        session.add(order_item)

        # Deduct from wallet
        user.wallet_balance -= total

        session.commit()

        # Notify user
        user_message = f"""✅ Purchase Successful!

💰 Total Amount: {format_price(total)}
📝 Order ID: #{order.id}

{order_details}
Thank you for your purchase!"""

        # Create keyboard with Home and Order History buttons
        keyboard = [
            [
                InlineKeyboardButton("🏠 Home", callback_data="main_menu"),
                InlineKeyboardButton("📋 Order History", callback_data="order_history")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(user_message, reply_markup=reply_markup)

        # Notify admin
        admin_message = f"""🛍 New Order Received

👤 User ID: {telegram_id}
💰 Amount: {format_price(total)}
📝 Order ID: #{order.id}

{order_details}"""

        await notify_admin(context, admin_message)


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
    ).limit(quantity).with_for_update().all()

    if len(available_keys) < quantity:
        raise ValueError(f"Not enough keys available. Requested: {quantity}, Available: {len(available_keys)}")

    assigned_keys = []
    for key in available_keys:
        key.is_sold = True
        key.order_id = order_id
        key.sold_at = datetime.utcnow()
        assigned_keys.append(key.key_value)

    session.commit()

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
