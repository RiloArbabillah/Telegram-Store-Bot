"""Direct order checkout lifecycle without wallet balance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from database.models import (
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductKey,
    ProductType,
    Transaction,
    TransactionStatus,
    User,
)
from utils import parse_supporting_files
from utils.helpers import get_effective_product_stock


class CheckoutError(Exception):
    """Raised when a direct checkout cannot be created or finalized."""


@dataclass
class CheckoutDelivery:
    user_telegram_id: int
    order_id: int
    amount: int
    order_details: str
    supporting_files: list[dict]


def _available_key_rows(session, product_id: int, quantity: int):
    return (
        session.query(ProductKey)
        .filter_by(product_id=product_id, is_sold=False, order_id=None)
        .order_by(ProductKey.id.asc())
        .limit(quantity)
        .with_for_update()
        .all()
    )


def _reserve_product(session, product: Product, quantity: int, order: Order) -> None:
    if quantity < 1:
        raise CheckoutError("Quantity must be at least 1.")
    if not product.is_active:
        raise CheckoutError("Product is not active.")
    if product.product_type == ProductType.FILE and quantity != 1:
        raise CheckoutError("File products can only be purchased one at a time.")
    if get_effective_product_stock(product, session=session) < quantity:
        raise CheckoutError("Not enough stock.")

    product.stock_count -= quantity

    if product.product_type in {ProductType.KEY, ProductType.AKUN}:
        rows = _available_key_rows(session, product.id, quantity)
        if len(rows) < quantity:
            product.stock_count += quantity
            raise CheckoutError("Not enough stock.")
        for row in rows:
            row.order = order


def create_pending_checkout(session, *, user_id: int, product_id: int, quantity: int) -> Order:
    """Create a pending order and reserve inventory for direct payment."""
    user = session.get(User, user_id)
    if not user:
        raise CheckoutError("User not found.")

    product = session.query(Product).filter_by(id=product_id).with_for_update().first()
    if not product:
        raise CheckoutError("Product not found.")

    total = int(product.price) * int(quantity)
    order = Order(user_id=user.id, total_amount=total, status=OrderStatus.PENDING_PAYMENT)
    session.add(order)
    session.flush()

    _reserve_product(session, product, int(quantity), order)

    order_item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        quantity=int(quantity),
        price=product.price,
    )
    session.add(order_item)
    session.flush()
    return order


def _release_reserved_order(session, order: Order) -> None:
    for item in order.order_items:
        if item.product:
            item.product.stock_count += item.quantity

    reserved_keys = session.query(ProductKey).filter_by(order_id=order.id, is_sold=False).all()
    for key in reserved_keys:
        key.order_id = None


def release_pending_order(session, order: Order) -> None:
    """Release inventory reserved by a pending order."""
    if order.status == OrderStatus.PENDING_PAYMENT:
        _release_reserved_order(session, order)


def expire_pending_checkout(session, transaction_id: int) -> Order:
    """Mark a pending transaction/order expired and release reserved stock."""
    transaction = session.get(Transaction, transaction_id)
    if not transaction:
        raise CheckoutError("Transaction not found.")

    if transaction.status == TransactionStatus.COMPLETED:
        raise CheckoutError("Completed payments cannot be expired.")

    order = transaction.order
    if not order:
        transaction.status = TransactionStatus.EXPIRED
        return None

    if order.status == OrderStatus.PENDING_PAYMENT:
        _release_reserved_order(session, order)
        order.status = OrderStatus.EXPIRED

    transaction.status = TransactionStatus.EXPIRED
    return order


def finalize_paid_checkout(session, transaction_id: int) -> CheckoutDelivery | None:
    """Complete a paid order transaction and return delivery details."""
    transaction = session.get(Transaction, transaction_id)
    if not transaction:
        raise CheckoutError("Transaction not found.")

    if transaction.status == TransactionStatus.COMPLETED:
        return None

    order = transaction.order
    if not order:
        raise CheckoutError("Transaction is not linked to an order.")
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise CheckoutError("Order is not awaiting payment.")

    user = order.user
    order_details = ""
    supporting_files_to_send: list[dict] = []

    for order_item in order.order_items:
        product = order_item.product
        if product.product_type in {ProductType.KEY, ProductType.AKUN}:
            reserved = (
                session.query(ProductKey)
                .filter_by(order_id=order.id, product_id=product.id, is_sold=False)
                .order_by(ProductKey.id.asc())
                .limit(order_item.quantity)
                .with_for_update()
                .all()
            )
            if len(reserved) < order_item.quantity:
                raise CheckoutError("Reserved stock is incomplete.")

            delivered_values = []
            for index, key in enumerate(reserved, start=1):
                key.is_sold = True
                key.sold_at = datetime.utcnow()
                delivered_values.append(key.key_value)
                if product.product_type == ProductType.AKUN:
                    for file_info in parse_supporting_files(key.supporting_files):
                        supporting_files_to_send.append(
                            {
                                **file_info,
                                "caption": (
                                    f"📎 {product.name} - Akun #{index} - "
                                    f"{file_info.get('file_name', 'Supporting file')}"
                                ),
                            }
                        )

            order_item.delivered_asset = "\n".join(delivered_values)
            label = "Akun" if product.product_type == ProductType.AKUN else "Keys"
            order_details += f"📦 {product.name} (x{order_item.quantity})\n🔐 {label}:\n{order_item.delivered_asset}\n"
            if product.product_type == ProductType.AKUN and supporting_files_to_send:
                order_details += f"📎 Supporting files: {len(supporting_files_to_send)} file(s) will be sent after this message.\n"

        elif product.product_type == ProductType.FILE:
            order_item.delivered_asset = product.download_link
            order_details += f"📦 {product.name}\n🔗 Download: {order_item.delivered_asset}\n"

    order.status = OrderStatus.COMPLETED
    order.completed_at = datetime.utcnow()
    transaction.status = TransactionStatus.COMPLETED
    transaction.confirmed_amount = int(transaction.confirmed_amount or transaction.amount)
    transaction.completed_at = datetime.utcnow()

    return CheckoutDelivery(
        user_telegram_id=user.telegram_id,
        order_id=order.id,
        amount=order.total_amount,
        order_details=order_details,
        supporting_files=supporting_files_to_send,
    )
