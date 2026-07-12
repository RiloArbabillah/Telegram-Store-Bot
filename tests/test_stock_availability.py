import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Order, OrderStatus, Product, ProductKey, ProductType, User
from utils.helpers import get_effective_product_stock


class StockAvailabilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_key_stock_excludes_reserved_pending_keys(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="License", price=12000, stock_count=2, product_type=ProductType.KEY)
            session.add_all([user, product])
            session.flush()
            order = Order(user_id=user.id, total_amount=12000, status=OrderStatus.PENDING_PAYMENT)
            session.add(order)
            session.flush()
            session.add_all(
                [
                    ProductKey(product_id=product.id, key_value="KEY-1", order_id=order.id),
                    ProductKey(product_id=product.id, key_value="KEY-2"),
                ]
            )

            self.assertEqual(get_effective_product_stock(product, session=session), 1)

    def test_akun_stock_excludes_reserved_pending_accounts(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            product = Product(name="Account", price=12000, stock_count=2, product_type=ProductType.AKUN)
            session.add_all([user, product])
            session.flush()
            order = Order(user_id=user.id, total_amount=12000, status=OrderStatus.PENDING_PAYMENT)
            session.add(order)
            session.flush()
            session.add_all(
                [
                    ProductKey(product_id=product.id, key_value="ACC-1", order_id=order.id),
                    ProductKey(product_id=product.id, key_value="ACC-2"),
                ]
            )

            self.assertEqual(get_effective_product_stock(product, session=session), 1)


if __name__ == "__main__":
    unittest.main()
