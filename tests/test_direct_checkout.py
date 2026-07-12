import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base,
    Order,
    OrderStatus,
    PaymentMethod,
    Product,
    ProductKey,
    ProductType,
    Transaction,
    TransactionStatus,
    User,
)
from services.direct_checkout import (
    CheckoutError,
    create_pending_checkout,
    expire_pending_checkout,
    finalize_paid_checkout,
)


class DirectCheckoutTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_checkout_reserves_key_stock_until_paid(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=2, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            session.add_all(
                [
                    ProductKey(product_id=product.id, key_value="KEY-1"),
                    ProductKey(product_id=product.id, key_value="KEY-2"),
                ]
            )
            user_id = user.id
            product_id = product.id

        with self.Session.begin() as session:
            order = create_pending_checkout(session, user_id=user_id, product_id=product_id, quantity=1)
            order_id = order.id

        with self.Session() as session:
            order = session.get(Order, order_id)
            product = session.get(Product, product_id)
            reserved_key = session.query(ProductKey).filter_by(order_id=order_id).one()

            self.assertEqual(order.status, OrderStatus.PENDING_PAYMENT)
            self.assertEqual(product.stock_count, 1)
            self.assertFalse(reserved_key.is_sold)

    def test_paid_checkout_completes_order_and_sells_reserved_keys(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=1, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            session.add(ProductKey(product_id=product.id, key_value="KEY-1"))
            order = create_pending_checkout(session, user_id=user.id, product_id=product.id, quantity=1)
            transaction = Transaction(
                user_id=user.id,
                order_id=order.id,
                amount=order.total_amount,
                payment_method=PaymentMethod.QRIS,
                status=TransactionStatus.PENDING,
            )
            session.add(transaction)
            session.flush()
            transaction_id = transaction.id

        with self.Session.begin() as session:
            result = finalize_paid_checkout(session, transaction_id)

        self.assertEqual(result.order_id, 1)
        self.assertIn("KEY-1", result.order_details)
        with self.Session() as session:
            order = session.query(Order).one()
            key = session.query(ProductKey).one()
            txn = session.query(Transaction).one()

            self.assertEqual(order.status, OrderStatus.COMPLETED)
            self.assertTrue(key.is_sold)
            self.assertEqual(txn.status, TransactionStatus.COMPLETED)

    def test_expired_checkout_releases_reserved_stock(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=1, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            session.add(ProductKey(product_id=product.id, key_value="KEY-1"))
            order = create_pending_checkout(session, user_id=user.id, product_id=product.id, quantity=1)
            transaction = Transaction(
                user_id=user.id,
                order_id=order.id,
                amount=order.total_amount,
                payment_method=PaymentMethod.QRIS,
                status=TransactionStatus.PENDING,
            )
            session.add(transaction)
            session.flush()
            transaction_id = transaction.id

        with self.Session.begin() as session:
            expire_pending_checkout(session, transaction_id)

        with self.Session() as session:
            order = session.query(Order).one()
            product = session.query(Product).one()
            key = session.query(ProductKey).one()
            txn = session.query(Transaction).one()

            self.assertEqual(order.status, OrderStatus.EXPIRED)
            self.assertEqual(product.stock_count, 1)
            self.assertIsNone(key.order_id)
            self.assertFalse(key.is_sold)
            self.assertEqual(txn.status, TransactionStatus.EXPIRED)

    def test_checkout_rejects_quantity_above_effective_stock_after_reservation(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=2, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            session.add_all(
                [
                    ProductKey(product_id=product.id, key_value="KEY-1"),
                    ProductKey(product_id=product.id, key_value="KEY-2"),
                ]
            )
            user_id = user.id
            product_id = product.id

        with self.Session.begin() as session:
            create_pending_checkout(session, user_id=user_id, product_id=product_id, quantity=1)

        with self.Session.begin() as session:
            with self.assertRaises(CheckoutError):
                create_pending_checkout(session, user_id=user_id, product_id=product_id, quantity=2)

    def test_checkout_rejects_unavailable_quantity(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=0, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            user_id = user.id
            product_id = product.id

        with self.Session.begin() as session:
            with self.assertRaises(CheckoutError):
                create_pending_checkout(session, user_id=user_id, product_id=product_id, quantity=1)


if __name__ == "__main__":
    unittest.main()
