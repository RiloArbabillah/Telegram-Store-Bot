import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    AdminAuditLog,
    Base,
    BroadcastDelivery,
    BroadcastJob,
    Dispute,
    DisputeStatus,
    Order,
    OrderStatus,
    PaymentMethod,
    Product,
    ProductKey,
    ProductType,
    StockAdjustment,
    Transaction,
    TransactionStatus,
    User,
)
from services.admin_operations import (
    AdminOperationError,
    cancel_order,
    confirm_transaction,
    create_broadcast_job,
    resolve_dispute,
    restock_product,
    set_user_banned,
)


class AdminOperationsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_cancel_order_refunds_only_once(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10, wallet_balance=100)
            session.add(user)
            session.flush()
            order = Order(user_id=user.id, total_amount=50, status=OrderStatus.PROCESSING)
            session.add(order)
            session.flush()
            order_id = order.id

        with self.Session.begin() as session:
            cancel_order(session, order_id, admin_id=123)
        with self.Session.begin() as session:
            with self.assertRaises(AdminOperationError):
                cancel_order(session, order_id, admin_id=123)

        with self.Session() as session:
            self.assertEqual(session.query(User).one().wallet_balance, 150)
            self.assertEqual(session.query(AdminAuditLog).count(), 1)

    def test_confirm_transaction_credits_only_once(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10, wallet_balance=0)
            session.add(user)
            session.flush()
            transaction = Transaction(
                user_id=user.id,
                amount=25000,
                payment_method=PaymentMethod.QRIS,
                status=TransactionStatus.PENDING,
            )
            session.add(transaction)
            session.flush()
            transaction_id = transaction.id

        with self.Session.begin() as session:
            confirm_transaction(session, transaction_id, admin_id=123)
        with self.Session.begin() as session:
            with self.assertRaises(AdminOperationError):
                confirm_transaction(session, transaction_id, admin_id=123)

        with self.Session() as session:
            self.assertEqual(session.query(User).one().wallet_balance, 25000)

    def test_restock_adds_keys_adjustment_and_audit(self):
        with self.Session.begin() as session:
            product = Product(
                name="Produk",
                price=1000,
                stock_count=0,
                product_type=ProductType.KEY,
            )
            session.add(product)
            session.flush()
            product_id = product.id

        with self.Session.begin() as session:
            added = restock_product(session, product_id, ["KEY-1", "KEY-2"], admin_id=123)

        self.assertEqual(added, 2)
        with self.Session() as session:
            self.assertEqual(session.query(ProductKey).count(), 2)
            self.assertEqual(session.query(Product).one().stock_count, 2)
            self.assertEqual(session.query(StockAdjustment).one().quantity, 2)
            self.assertEqual(session.query(AdminAuditLog).count(), 1)

    def test_ban_and_resolve_dispute_are_audited(self):
        with self.Session.begin() as session:
            user = User(telegram_id=10)
            session.add(user)
            session.flush()
            order = Order(user_id=user.id, total_amount=1000)
            session.add(order)
            session.flush()
            dispute = Dispute(
                order_id=order.id,
                user_id=user.id,
                reason="Tidak sesuai",
                status=DisputeStatus.OPENED,
            )
            session.add(dispute)
            session.flush()
            user_id = user.id
            dispute_id = dispute.id

        with self.Session.begin() as session:
            set_user_banned(session, user_id, True, admin_id=123)
            resolve_dispute(session, dispute_id, "Sudah diganti", admin_id=123)

        with self.Session() as session:
            self.assertTrue(session.get(User, user_id).is_banned)
            self.assertEqual(session.get(Dispute, dispute_id).status, DisputeStatus.RESOLVED)
            self.assertEqual(session.get(Dispute, dispute_id).admin_notes, "Sudah diganti")
            self.assertEqual(session.query(AdminAuditLog).count(), 2)

    def test_broadcast_snapshots_only_unbanned_users(self):
        with self.Session.begin() as session:
            session.add_all([
                User(telegram_id=10, is_banned=False),
                User(telegram_id=11, is_banned=True),
                User(telegram_id=12, is_banned=False),
            ])

        with self.Session.begin() as session:
            job = create_broadcast_job(session, "Pengumuman", None, admin_id=123)
            job_id = job.id

        with self.Session() as session:
            job = session.get(BroadcastJob, job_id)
            self.assertEqual(job.target_count, 2)
            self.assertEqual(session.query(BroadcastDelivery).count(), 2)


if __name__ == "__main__":
    unittest.main()
