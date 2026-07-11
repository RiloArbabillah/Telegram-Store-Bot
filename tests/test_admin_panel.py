import unittest
import io
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from admin_panel import create_admin_blueprint
from database.models import Base
from database.models import (
    Category,
    BroadcastJob,
    Dispute,
    DisputeStatus,
    Order,
    PaymentMethod,
    Product,
    ProductType,
    Settings,
    Transaction,
    TransactionStatus,
    User,
)
from services.admin_auth import create_admin_otp


class AdminPanelAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.asset_dir = tempfile.TemporaryDirectory()
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

        @contextmanager
        def session_provider():
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        config = SimpleNamespace(
            ADMIN_TELEGRAM_ID=123,
            ADMIN_TELEGRAM_USERNAME="admin",
            ADMIN_SESSION_SECRET="s" * 32,
            ADMIN_COOKIE_SECURE=False,
            WEBHOOK_BASE_URL="https://bot.example.com",
            ASSETS_DIR=self.asset_dir.name,
            UPLOADS_DIR=self.asset_dir.name,
        )
        app = Flask(__name__)
        app.secret_key = config.ADMIN_SESSION_SECRET
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        app.register_blueprint(create_admin_blueprint(config, session_provider))
        self.client = app.test_client()

        with self.Session.begin() as db_session:
            user = User(telegram_id=999, username="pembeli")
            category = Category(name="Software")
            db_session.add_all([user, category, Settings(welcome_message="Halo")])
            db_session.flush()
            product = Product(
                name="Produk Awal",
                price=10000,
                stock_count=0,
                product_type=ProductType.KEY,
                category_id=category.id,
            )
            order = Order(user_id=user.id, total_amount=10000)
            transaction = Transaction(
                user_id=user.id,
                amount=20000,
                payment_method=PaymentMethod.QRIS,
                status=TransactionStatus.PENDING,
            )
            db_session.add_all([product, order, transaction])
            db_session.flush()
            db_session.add(
                Dispute(
                    order_id=order.id,
                    user_id=user.id,
                    reason="Barang tidak sesuai",
                    status=DisputeStatus.OPENED,
                )
            )

    def tearDown(self):
        self.asset_dir.cleanup()

    def csrf_token(self):
        self.client.get("/admin/login")
        with self.client.session_transaction() as browser_session:
            return browser_session["admin_csrf_token"]

    def issue_otp(self, admin_id=123):
        with self.Session.begin() as session:
            return create_admin_otp(session, admin_id, secret="s" * 32)

    def login(self, otp):
        return self.client.post(
            "/admin/session",
            data={"otp": otp, "csrf_token": self.csrf_token()},
        )

    def authenticate(self):
        with self.client.session_transaction() as browser_session:
            browser_session["admin_telegram_id"] = 123
            browser_session["admin_csrf_token"] = "csrf-test"
            browser_session.permanent = True

    def post(self, path, data=None):
        payload = dict(data or {})
        payload["csrf_token"] = "csrf-test"
        return self.client.post(path, data=payload)

    def test_protected_route_redirects_to_login(self):
        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))

    def test_login_page_renders_otp_form(self):
        response = self.client.get("/admin/login")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="otp"', body)
        self.assertIn('maxlength="8"', body)
        self.assertNotIn("login.js", body)

    def test_valid_otp_creates_admin_session(self):
        response = self.login(self.issue_otp())

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin"))
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["admin_telegram_id"], 123)
            self.assertTrue(browser_session.permanent)

        dashboard = self.client.get("/admin")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Dashboard", dashboard.get_data(as_text=True))

    def test_replayed_otp_is_rejected(self):
        otp = self.issue_otp()
        self.assertEqual(self.login(otp).status_code, 302)
        self.client.post("/admin/logout", data={"csrf_token": self.csrf_token()})

        response = self.login(otp)

        self.assertEqual(response.status_code, 401)

    def test_post_without_csrf_is_rejected(self):
        self.login(self.issue_otp())

        response = self.client.post("/admin/logout")

        self.assertEqual(response.status_code, 400)

    def test_session_for_other_admin_is_invalidated(self):
        with self.client.session_transaction() as browser_session:
            browser_session["admin_telegram_id"] = 456

        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))

    def test_all_primary_admin_pages_render(self):
        self.authenticate()

        for path in (
            "/admin",
            "/admin/products",
            "/admin/categories",
            "/admin/users",
            "/admin/orders",
            "/admin/transactions",
            "/admin/disputes",
            "/admin/settings",
            "/admin/broadcasts",
            "/admin/audit",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_product_create_restock_user_ban_settings_and_broadcast(self):
        self.authenticate()
        with self.Session() as session:
            category_id = session.query(Category).one().id
            user_id = session.query(User).one().id

        response = self.post(
            "/admin/products/new",
            {
                "name": "Produk Baru",
                "description": "Deskripsi",
                "price": "25000",
                "product_type": "key",
                "category_id": str(category_id),
                "is_active": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.Session() as session:
            product = session.query(Product).filter_by(name="Produk Baru").one()
            product_id = product.id

        self.assertEqual(
            self.post(f"/admin/products/{product_id}/restock", {"items": "A\nB"}).status_code,
            302,
        )
        self.assertEqual(self.post(f"/admin/users/{user_id}/ban").status_code, 302)
        self.assertEqual(
            self.post(
                "/admin/settings",
                {"welcome_message": "Selamat datang", "support_username": "support"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.post("/admin/broadcasts", {"message_text": "Pengumuman"}).status_code,
            302,
        )

        with self.Session() as session:
            self.assertEqual(session.get(Product, product_id).stock_count, 2)
            self.assertTrue(session.get(User, user_id).is_banned)
            self.assertEqual(session.query(Settings).one().welcome_message, "Selamat datang")

    def test_broadcast_accepts_an_image_upload(self):
        from PIL import Image

        self.authenticate()
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
        image.seek(0)

        response = self.client.post(
            "/admin/broadcasts",
            data={
                "csrf_token": "csrf-test",
                "message_text": "Dengan gambar",
                "image": (image, "info.png"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with self.Session() as session:
            self.assertTrue(session.query(BroadcastJob).order_by(BroadcastJob.id.desc()).first().image_path)

    def test_detail_pages_render_and_used_category_cannot_be_deleted(self):
        self.authenticate()
        with self.Session() as session:
            product = session.query(Product).one()
            user = session.query(User).one()
            order = session.query(Order).one()
            transaction = session.query(Transaction).one()
            dispute = session.query(Dispute).one()
            category = session.query(Category).one()
            paths = (
                f"/admin/products/{product.id}/stock",
                f"/admin/users/{user.id}",
                f"/admin/orders/{order.id}",
                f"/admin/transactions/{transaction.id}",
                f"/admin/disputes/{dispute.id}",
            )
            category_id = category.id

        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        response = self.post(f"/admin/categories/{category_id}/delete")
        self.assertEqual(response.status_code, 302)
        with self.Session() as session:
            self.assertIsNotNone(session.get(Category, category_id))


if __name__ == "__main__":
    unittest.main()
