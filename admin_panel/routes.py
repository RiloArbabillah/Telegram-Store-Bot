"""Authentication and page routes for the administration panel."""

from __future__ import annotations

import secrets
from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_

from database.models import (
    AdminAuditLog,
    BroadcastJob,
    Category,
    Dispute,
    DisputeStatus,
    Order,
    OrderStatus,
    Product,
    ProductKey,
    ProductType,
    Settings as StoreSettings,
    Subcategory,
    StockAdjustment,
    Transaction,
    TransactionStatus,
    User,
)
from services.admin_auth import consume_login_token
from services.admin_broadcasts import retry_failed_broadcast
from services.admin_operations import (
    AdminOperationError,
    cancel_order,
    cancel_transaction,
    complete_order,
    confirm_transaction,
    create_broadcast_job,
    record_audit,
    resolve_dispute,
    restock_product,
    set_user_banned,
)
from services.admin_uploads import UploadError, save_document, save_image
from utils import dump_supporting_files, format_price


def create_admin_blueprint(config, session_provider):
    panel = Blueprint(
        "admin_panel",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/admin/static",
    )

    @panel.record_once
    def configure(state):
        state.app.config.update(
            SECRET_KEY=config.ADMIN_SESSION_SECRET,
            PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            SESSION_COOKIE_SECURE=config.ADMIN_COOKIE_SECURE,
        )

    def csrf_token():
        token = session.get("admin_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["admin_csrf_token"] = token
        return token

    def is_authenticated():
        return session.get("admin_telegram_id") == config.ADMIN_TELEGRAM_ID

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not is_authenticated():
                session.pop("admin_telegram_id", None)
                return redirect(url_for("admin_panel.login"))
            return view(*args, **kwargs)

        return wrapped

    def pagination(query, *, per_page=20):
        try:
            page = max(int(request.args.get("page", 1)), 1)
        except ValueError:
            page = 1
        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()
        return {
            "rows": rows,
            "page": page,
            "pages": max((total + per_page - 1) // per_page, 1),
            "total": total,
        }

    def run_operation(operation):
        try:
            with session_provider() as db_session:
                result = operation(db_session)
            return result
        except AdminOperationError as exc:
            flash(str(exc), "error")
            return None

    @panel.context_processor
    def inject_panel_context():
        return {
            "csrf_token": csrf_token,
            "admin_username": config.ADMIN_TELEGRAM_USERNAME or str(config.ADMIN_TELEGRAM_ID),
            "format_price": format_price,
        }

    @panel.before_request
    def protect_mutations():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("admin_csrf_token")
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not expected or not supplied or not secrets.compare_digest(expected, supplied):
                abort(400, description="Permintaan tidak valid. Muat ulang halaman dan coba lagi.")

    @panel.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'",
        )
        return response

    @panel.get("/admin/login")
    def login():
        if is_authenticated():
            return redirect(url_for("admin_panel.dashboard"))
        return render_template("admin/login.html")

    @panel.post("/admin/session")
    def create_session():
        raw_token = request.form.get("token", "")
        with session_provider() as db_session:
            admin_id = consume_login_token(
                db_session,
                raw_token,
                expected_admin_id=config.ADMIN_TELEGRAM_ID,
            )
        if admin_id is None:
            return render_template("admin/login.html", login_error=True), 401

        session.clear()
        session["admin_telegram_id"] = admin_id
        session["admin_csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect(url_for("admin_panel.dashboard"))

    @panel.post("/admin/logout")
    @login_required
    def logout():
        session.clear()
        flash("Sesi admin telah diakhiri.", "success")
        return redirect(url_for("admin_panel.login"))

    @panel.get("/admin")
    @login_required
    def dashboard():
        with session_provider() as db_session:
            metrics = {
                "users": db_session.query(User).count(),
                "revenue": db_session.query(func.coalesce(func.sum(Transaction.confirmed_amount), 0))
                .filter(Transaction.status == TransactionStatus.COMPLETED)
                .scalar(),
                "pending_orders": db_session.query(Order).filter_by(status=OrderStatus.PROCESSING).count(),
                "pending_transactions": db_session.query(Transaction).filter_by(status=TransactionStatus.PENDING).count(),
                "open_disputes": db_session.query(Dispute).filter_by(status=DisputeStatus.OPENED).count(),
                "low_stock": db_session.query(Product).filter(Product.is_active.is_(True), Product.stock_count <= 5).count(),
            }
            recent_orders = db_session.query(Order).order_by(Order.created_at.desc()).limit(6).all()
            return render_template(
                "admin/dashboard.html",
                active_page="dashboard",
                metrics=metrics,
                recent_orders=recent_orders,
            )

    @panel.get("/admin/products")
    @login_required
    def products():
        search = request.args.get("q", "").strip()
        with session_provider() as db_session:
            query = db_session.query(Product).order_by(Product.created_at.desc())
            if search:
                query = query.filter(Product.name.ilike(f"%{search}%"))
            return render_template(
                "admin/products.html",
                active_page="products",
                data=pagination(query),
                search=search,
            )

    def product_form_context(db_session, product=None):
        return {
            "product": product,
            "categories": db_session.query(Category).order_by(Category.name).all(),
            "subcategories": db_session.query(Subcategory).order_by(Subcategory.name).all(),
            "product_types": list(ProductType),
            "active_page": "products",
        }

    def apply_product_form(db_session, product):
        name = request.form.get("name", "").strip()
        if not name:
            raise AdminOperationError("Nama produk wajib diisi.")
        try:
            price = int(request.form.get("price", "0"))
        except ValueError as exc:
            raise AdminOperationError("Harga produk tidak valid.") from exc
        if price <= 0:
            raise AdminOperationError("Harga produk harus lebih dari nol.")
        try:
            product_type = ProductType(request.form.get("product_type", ""))
        except ValueError as exc:
            raise AdminOperationError("Tipe produk tidak valid.") from exc
        product.name = name
        product.description = request.form.get("description", "").strip() or None
        product.price = price
        product.product_type = product_type
        product.category_id = int(request.form["category_id"]) if request.form.get("category_id") else None
        product.subcategory_id = int(request.form["subcategory_id"]) if request.form.get("subcategory_id") else None
        product.download_link = request.form.get("download_link", "").strip() or None
        product.is_active = request.form.get("is_active") == "1"
        image = request.files.get("image")
        if image and image.filename:
            product.image_path = save_image(image, getattr(config, "ASSETS_DIR", "assets"), "products")

    @panel.route("/admin/products/new", methods=["GET", "POST"])
    @login_required
    def product_new():
        with session_provider() as db_session:
            if request.method == "POST":
                try:
                    product = Product(stock_count=0)
                    apply_product_form(db_session, product)
                    db_session.add(product)
                    db_session.flush()
                    record_audit(
                        db_session,
                        admin_id=config.ADMIN_TELEGRAM_ID,
                        action="product.create",
                        entity_type="product",
                        entity_id=product.id,
                    )
                except (AdminOperationError, UploadError) as exc:
                    flash(str(exc), "error")
                    return render_template("admin/product_form.html", **product_form_context(db_session)), 400
                flash("Produk berhasil dibuat.", "success")
                return redirect(url_for("admin_panel.products"))
            return render_template("admin/product_form.html", **product_form_context(db_session))

    @panel.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        with session_provider() as db_session:
            product = db_session.get(Product, product_id)
            if not product:
                abort(404)
            if request.method == "POST":
                try:
                    apply_product_form(db_session, product)
                    record_audit(
                        db_session,
                        admin_id=config.ADMIN_TELEGRAM_ID,
                        action="product.update",
                        entity_type="product",
                        entity_id=product.id,
                    )
                except (AdminOperationError, UploadError) as exc:
                    flash(str(exc), "error")
                    return render_template("admin/product_form.html", **product_form_context(db_session, product)), 400
                flash("Produk berhasil diperbarui.", "success")
                return redirect(url_for("admin_panel.products"))
            return render_template("admin/product_form.html", **product_form_context(db_session, product))

    @panel.post("/admin/products/<int:product_id>/restock")
    @login_required
    def product_restock(product_id):
        items = request.form.get("items", "").splitlines()
        supporting_files = None
        upload = request.files.get("supporting_file")
        if upload and upload.filename:
            try:
                path = save_document(upload, getattr(config, "UPLOADS_DIR", "uploads"), "supporting")
            except UploadError as exc:
                flash(str(exc), "error")
                return redirect(url_for("admin_panel.products"))
            supporting_files = dump_supporting_files([{
                "storage_path": path,
                "file_name": upload.filename,
                "mime_type": upload.mimetype or "",
                "file_type": "photo" if (upload.mimetype or "").startswith("image/") else "document",
            }])
        result = run_operation(
            lambda db_session: restock_product(
                db_session,
                product_id,
                items,
                admin_id=config.ADMIN_TELEGRAM_ID,
                supporting_files=supporting_files,
            )
        )
        if result is not None:
            flash(f"{result} item stok berhasil ditambahkan.", "success")
        return redirect(url_for("admin_panel.products"))

    @panel.get("/admin/products/<int:product_id>/stock")
    @login_required
    def product_stock(product_id):
        with session_provider() as db_session:
            product = db_session.get(Product, product_id)
            if not product:
                abort(404)
            adjustments = db_session.query(StockAdjustment).filter_by(product_id=product.id).order_by(StockAdjustment.created_at.desc()).all()
            available_keys = db_session.query(ProductKey).filter_by(product_id=product.id, is_sold=False).count()
            sold_keys = db_session.query(ProductKey).filter_by(product_id=product.id, is_sold=True).count()
            return render_template("admin/stock.html", active_page="products", product=product, adjustments=adjustments, available_keys=available_keys, sold_keys=sold_keys)

    @panel.get("/admin/categories")
    @login_required
    def categories():
        with session_provider() as db_session:
            items = db_session.query(Category).order_by(Category.name).all()
            return render_template("admin/categories.html", active_page="categories", categories=items)

    @panel.post("/admin/categories")
    @login_required
    def category_create():
        name = request.form.get("name", "").strip()
        if not name:
            flash("Nama kategori wajib diisi.", "error")
        else:
            with session_provider() as db_session:
                category = Category(name=name, description=request.form.get("description", "").strip() or None)
                db_session.add(category)
                db_session.flush()
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="category.create", entity_type="category", entity_id=category.id)
            flash("Kategori berhasil dibuat.", "success")
        return redirect(url_for("admin_panel.categories"))

    @panel.post("/admin/categories/<int:category_id>/update")
    @login_required
    def category_update(category_id):
        with session_provider() as db_session:
            category = db_session.get(Category, category_id)
            if not category:
                abort(404)
            name = request.form.get("name", "").strip()
            if not name:
                flash("Nama kategori wajib diisi.", "error")
            else:
                category.name = name
                category.description = request.form.get("description", "").strip() or None
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="category.update", entity_type="category", entity_id=category.id)
                flash("Kategori berhasil diperbarui.", "success")
        return redirect(url_for("admin_panel.categories"))

    @panel.post("/admin/categories/<int:category_id>/delete")
    @login_required
    def category_delete(category_id):
        with session_provider() as db_session:
            category = db_session.get(Category, category_id)
            if not category:
                abort(404)
            if category.products or category.subcategories:
                flash("Kategori masih dipakai produk atau subkategori dan tidak dapat dihapus.", "error")
            else:
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="category.delete", entity_type="category", entity_id=category.id)
                db_session.delete(category)
                flash("Kategori berhasil dihapus.", "success")
        return redirect(url_for("admin_panel.categories"))

    @panel.post("/admin/subcategories")
    @login_required
    def subcategory_create():
        name = request.form.get("name", "").strip()
        category_id = request.form.get("category_id")
        if not name or not category_id:
            flash("Nama dan kategori induk wajib diisi.", "error")
        else:
            with session_provider() as db_session:
                item = Subcategory(name=name, category_id=int(category_id))
                db_session.add(item)
                db_session.flush()
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="subcategory.create", entity_type="subcategory", entity_id=item.id)
            flash("Subkategori berhasil dibuat.", "success")
        return redirect(url_for("admin_panel.categories"))

    @panel.post("/admin/subcategories/<int:subcategory_id>/delete")
    @login_required
    def subcategory_delete(subcategory_id):
        with session_provider() as db_session:
            item = db_session.get(Subcategory, subcategory_id)
            if not item:
                abort(404)
            if item.products:
                flash("Subkategori masih dipakai produk dan tidak dapat dihapus.", "error")
            else:
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="subcategory.delete", entity_type="subcategory", entity_id=item.id)
                db_session.delete(item)
                flash("Subkategori berhasil dihapus.", "success")
        return redirect(url_for("admin_panel.categories"))

    @panel.get("/admin/users")
    @login_required
    def users():
        search = request.args.get("q", "").strip()
        with session_provider() as db_session:
            query = db_session.query(User).order_by(User.created_at.desc())
            if search:
                query = query.filter(or_(User.username.ilike(f"%{search}%"), User.telegram_id == int(search) if search.isdigit() else False))
            return render_template("admin/users.html", active_page="users", data=pagination(query), search=search)

    @panel.get("/admin/users/<int:user_id>")
    @login_required
    def user_detail(user_id):
        with session_provider() as db_session:
            user = db_session.get(User, user_id)
            if not user:
                abort(404)
            details = [("Telegram ID", user.telegram_id), ("Username", f"@{user.username}" if user.username else "-"), ("Saldo", format_price(user.wallet_balance)), ("Status", "Diblokir" if user.is_banned else "Aktif")]
            related = [(f"Pesanan #{order.id}", f"{format_price(order.total_amount)} · {order.status.value}") for order in db_session.query(Order).filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(10)]
            return render_template("admin/detail.html", active_page="users", title="Detail pengguna", details=details, related_title="Aktivitas pesanan", related=related, back_url=url_for("admin_panel.users"))

    @panel.post("/admin/users/<int:user_id>/<action>")
    @login_required
    def user_status(user_id, action):
        if action not in {"ban", "unban"}:
            abort(404)
        result = run_operation(lambda db_session: set_user_banned(db_session, user_id, action == "ban", admin_id=config.ADMIN_TELEGRAM_ID))
        if result is not None:
            flash("Status pengguna berhasil diperbarui.", "success")
        return redirect(url_for("admin_panel.users"))

    @panel.get("/admin/orders")
    @login_required
    def orders():
        status = request.args.get("status", "")
        with session_provider() as db_session:
            query = db_session.query(Order).order_by(Order.created_at.desc())
            if status:
                try:
                    query = query.filter(Order.status == OrderStatus(status))
                except ValueError:
                    pass
            return render_template("admin/orders.html", active_page="orders", data=pagination(query), statuses=list(OrderStatus), selected_status=status)

    @panel.get("/admin/orders/<int:order_id>")
    @login_required
    def order_detail(order_id):
        with session_provider() as db_session:
            order = db_session.get(Order, order_id)
            if not order:
                abort(404)
            details = [("ID", f"#{order.id}"), ("Pengguna", f"@{order.user.username or order.user.telegram_id}"), ("Total", format_price(order.total_amount)), ("Status", order.status.value), ("Sengketa", order.dispute_status.value)]
            related = [(item.product.name, f"{item.quantity} × {format_price(item.price)}") for item in order.order_items]
            return render_template("admin/detail.html", active_page="orders", title="Detail pesanan", details=details, related_title="Item pesanan", related=related, back_url=url_for("admin_panel.orders"))

    @panel.post("/admin/orders/<int:order_id>/<action>")
    @login_required
    def order_action(order_id, action):
        operations = {"complete": complete_order, "cancel": cancel_order}
        if action not in operations:
            abort(404)
        result = run_operation(lambda db_session: operations[action](db_session, order_id, admin_id=config.ADMIN_TELEGRAM_ID))
        if result is not None:
            flash("Status pesanan berhasil diperbarui.", "success")
        return redirect(url_for("admin_panel.orders"))

    @panel.get("/admin/transactions")
    @login_required
    def transactions():
        status = request.args.get("status", "")
        with session_provider() as db_session:
            query = db_session.query(Transaction).order_by(Transaction.created_at.desc())
            if status:
                try:
                    query = query.filter(Transaction.status == TransactionStatus(status))
                except ValueError:
                    pass
            return render_template("admin/transactions.html", active_page="transactions", data=pagination(query), statuses=list(TransactionStatus), selected_status=status)

    @panel.get("/admin/transactions/<int:transaction_id>")
    @login_required
    def transaction_detail(transaction_id):
        with session_provider() as db_session:
            txn = db_session.get(Transaction, transaction_id)
            if not txn:
                abort(404)
            details = [("ID", f"#{txn.id}"), ("Pengguna", f"@{txn.user.username or txn.user.telegram_id}"), ("Nominal diminta", format_price(txn.amount)), ("Nominal diterima", format_price(txn.confirmed_amount or 0)), ("Metode", txn.payment_method.value), ("Provider", txn.provider_name or "-"), ("Status", txn.status.value), ("Referensi", txn.external_reference or "-")]
            return render_template("admin/detail.html", active_page="transactions", title="Detail transaksi", details=details, related_title=None, related=[], back_url=url_for("admin_panel.transactions"))

    @panel.post("/admin/transactions/<int:transaction_id>/<action>")
    @login_required
    def transaction_action(transaction_id, action):
        operations = {"confirm": confirm_transaction, "cancel": cancel_transaction}
        if action not in operations:
            abort(404)
        result = run_operation(lambda db_session: operations[action](db_session, transaction_id, admin_id=config.ADMIN_TELEGRAM_ID))
        if result is not None:
            flash("Transaksi berhasil diperbarui.", "success")
        return redirect(url_for("admin_panel.transactions"))

    @panel.get("/admin/disputes")
    @login_required
    def disputes():
        with session_provider() as db_session:
            query = db_session.query(Dispute).order_by(Dispute.created_at.desc())
            return render_template("admin/disputes.html", active_page="disputes", data=pagination(query))

    @panel.get("/admin/disputes/<int:dispute_id>")
    @login_required
    def dispute_detail(dispute_id):
        with session_provider() as db_session:
            dispute = db_session.get(Dispute, dispute_id)
            if not dispute:
                abort(404)
            details = [("ID", f"#{dispute.id}"), ("Pesanan", f"#{dispute.order_id}"), ("Pengguna", f"@{dispute.user.username or dispute.user.telegram_id}"), ("Status", dispute.status.value), ("Alasan", dispute.reason), ("Catatan admin", dispute.admin_notes or "-")]
            return render_template("admin/detail.html", active_page="disputes", title="Detail sengketa", details=details, related_title=None, related=[], back_url=url_for("admin_panel.disputes"))

    @panel.post("/admin/disputes/<int:dispute_id>/resolve")
    @login_required
    def dispute_resolve(dispute_id):
        result = run_operation(lambda db_session: resolve_dispute(db_session, dispute_id, request.form.get("admin_notes", ""), admin_id=config.ADMIN_TELEGRAM_ID))
        if result is not None:
            flash("Sengketa berhasil diselesaikan.", "success")
        return redirect(url_for("admin_panel.disputes"))

    @panel.route("/admin/settings", methods=["GET", "POST"])
    @login_required
    def store_settings():
        with session_provider() as db_session:
            store = db_session.query(StoreSettings).first()
            if not store:
                store = StoreSettings()
                db_session.add(store)
                db_session.flush()
            if request.method == "POST":
                store.welcome_message = request.form.get("welcome_message", "").strip()
                store.support_username = request.form.get("support_username", "").strip() or None
                store.channel_username = request.form.get("channel_username", "").strip() or None
                store.qris_instructions_text = request.form.get("qris_instructions_text", "").strip() or None
                store.qris_static_payload = request.form.get("qris_static_payload", "").strip() or None
                logo = request.files.get("store_logo")
                qris_image = request.files.get("qris_image")
                try:
                    if logo and logo.filename:
                        store.store_logo_path = save_image(logo, getattr(config, "ASSETS_DIR", "assets"), "logos")
                    if qris_image and qris_image.filename:
                        store.qris_image_file_id = save_image(qris_image, getattr(config, "ASSETS_DIR", "assets"), "qris")
                except UploadError as exc:
                    flash(str(exc), "error")
                    return render_template("admin/settings.html", active_page="settings", store=store), 400
                record_audit(db_session, admin_id=config.ADMIN_TELEGRAM_ID, action="settings.update", entity_type="settings", entity_id=store.id)
                flash("Pengaturan toko berhasil disimpan.", "success")
                return redirect(url_for("admin_panel.store_settings"))
            return render_template("admin/settings.html", active_page="settings", store=store)

    @panel.route("/admin/broadcasts", methods=["GET", "POST"])
    @login_required
    def broadcasts():
        if request.method == "POST":
            image_path = None
            image = request.files.get("image")
            if image and image.filename:
                try:
                    image_path = save_image(image, getattr(config, "ASSETS_DIR", "assets"), "broadcasts")
                except UploadError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin_panel.broadcasts"))
            result = run_operation(lambda db_session: create_broadcast_job(db_session, request.form.get("message_text", ""), image_path, admin_id=config.ADMIN_TELEGRAM_ID))
            if result is not None:
                flash("Broadcast masuk antrean pengiriman.", "success")
            return redirect(url_for("admin_panel.broadcasts"))
        with session_provider() as db_session:
            query = db_session.query(BroadcastJob).order_by(BroadcastJob.created_at.desc())
            return render_template("admin/broadcasts.html", active_page="broadcasts", data=pagination(query))

    @panel.post("/admin/broadcasts/<int:job_id>/retry")
    @login_required
    def broadcast_retry(job_id):
        result = run_operation(lambda db_session: retry_failed_broadcast(db_session, job_id, admin_id=config.ADMIN_TELEGRAM_ID))
        if result is not None:
            flash(f"{result} pengiriman gagal dimasukkan kembali ke antrean.", "success")
        return redirect(url_for("admin_panel.broadcasts"))

    @panel.get("/admin/audit")
    @login_required
    def audit():
        with session_provider() as db_session:
            query = db_session.query(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
            return render_template("admin/audit.html", active_page="audit", data=pagination(query))

    return panel
