"""Database models for the Telegram digital products store bot."""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()


class ProductType(enum.Enum):
    """Enum for product types."""
    KEY = "key"
    FILE = "file"
    AKUN = "akun"


class OrderStatus(enum.Enum):
    """Enum for order status."""
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class DisputeStatus(enum.Enum):
    """Enum for dispute status."""
    NIL = "NIL"
    OPENED = "Opened"
    RESOLVED = "Resolved"


class TransactionStatus(enum.Enum):
    """Enum for transaction/payment status."""
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


class PaymentMethod(enum.Enum):
    """Enum for payment methods."""
    CRYPTO_WALLET = "crypto_wallet"
    CARD = "card"
    QRIS = "qris"


class User(Base):
    """User model for storing customer information."""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(255))
    wallet_balance = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    orders = relationship("Order", back_populates="user")
    cart_items = relationship("Cart", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user")


class Category(Base):
    """Category model for product organization."""
    __tablename__ = 'categories'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    products = relationship("Product", back_populates="category")
    subcategories = relationship("Subcategory", back_populates="category")


class Subcategory(Base):
    """Subcategory model for additional product organization."""
    __tablename__ = 'subcategories'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    category = relationship("Category", back_populates="subcategories")
    products = relationship("Product", back_populates="subcategory")


class Product(Base):
    """Product model for items available for purchase."""
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    price = Column(Integer, nullable=False)
    stock_count = Column(Integer, default=0)
    product_type = Column(Enum(ProductType), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'), nullable=True)
    subcategory_id = Column(Integer, ForeignKey('subcategories.id'), nullable=True)
    image_path = Column(String(500), nullable=True)
    download_link = Column(String(500), nullable=True)  # For file-type products
    supporting_files = Column(Text, nullable=True)  # JSON list of Telegram files for AKUN products
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    category = relationship("Category", back_populates="products")
    subcategory = relationship("Subcategory", back_populates="products")
    product_keys = relationship("ProductKey", back_populates="product", cascade="all, delete-orphan")
    cart_items = relationship("Cart", back_populates="product")
    order_items = relationship("OrderItem", back_populates="product")


class ProductKey(Base):
    """SEPARATE TABLE for storing product keys inventory."""
    __tablename__ = 'product_keys'

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    key_value = Column(Text, nullable=False)
    supporting_files = Column(Text, nullable=True)  # JSON list of Telegram files for this AKUN item
    is_sold = Column(Boolean, default=False, index=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sold_at = Column(DateTime, nullable=True)

    # Relationships
    product = relationship("Product", back_populates="product_keys")
    order = relationship("Order", back_populates="assigned_keys")


class Cart(Base):
    """Shopping cart model for temporary product storage."""
    __tablename__ = 'cart'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="cart_items")
    product = relationship("Product", back_populates="cart_items")


class Order(Base):
    """Order model for purchase records."""
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    total_amount = Column(Integer, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PROCESSING)
    dispute_status = Column(Enum(DisputeStatus), default=DisputeStatus.NIL)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    assigned_keys = relationship("ProductKey", back_populates="order")
    disputes = relationship("Dispute", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    """Order items model for individual line items in orders."""
    __tablename__ = 'order_items'

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Integer, nullable=False)
    delivered_asset = Column(Text, nullable=True)  # Keys or download link
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="order_items")
    product = relationship("Product", back_populates="order_items")


class Transaction(Base):
    """Transaction model for wallet funding history."""
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    provider_name = Column(String(100), nullable=True)
    external_reference = Column(String(255), nullable=True)
    checkout_url = Column(String(500), nullable=True)
    qr_payload = Column(Text, nullable=True)
    provider_metadata = Column(Text, nullable=True)
    proof_file_id = Column(String(255), nullable=True)
    proof_file_type = Column(String(50), nullable=True)
    proof_submitted_at = Column(DateTime, nullable=True)
    confirmed_amount = Column(Integer, nullable=True)
    provider_status_code = Column(String(50), nullable=True)
    provider_status_text = Column(String(255), nullable=True)
    provider_paid_at = Column(DateTime, nullable=True)
    callback_received_at = Column(DateTime, nullable=True)
    crypto_address = Column(String(500), nullable=True)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="transactions")


class Settings(Base):
    """Settings model for store configuration (single row table)."""
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)
    welcome_message = Column(Text, default="Welcome to our digital store!")
    store_logo_path = Column(String(500), nullable=True)
    support_username = Column(String(255), nullable=True)
    channel_username = Column(String(255), nullable=True)
    qris_instructions_text = Column(Text, nullable=True)
    qris_image_file_id = Column(String(255), nullable=True)
    qris_static_payload = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Broadcast(Base):
    """Broadcast model for tracking broadcast messages."""
    __tablename__ = 'broadcasts'

    id = Column(Integer, primary_key=True)
    message_text = Column(Text, nullable=False)
    image_path = Column(String(500), nullable=True)
    sent_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Dispute(Base):
    """Dispute model for order disputes."""
    __tablename__ = 'disputes'

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    status = Column(Enum(DisputeStatus), default=DisputeStatus.OPENED)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    admin_notes = Column(Text, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="disputes")
    user = relationship("User")


class AdminLoginToken(Base):
    """Short-lived, one-time token issued to the configured Telegram admin."""
    __tablename__ = 'admin_login_tokens'

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    admin_telegram_id = Column(Integer, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AdminAuditLog(Base):
    """Non-sensitive record of mutations made from an admin surface."""
    __tablename__ = 'admin_audit_logs'

    id = Column(Integer, primary_key=True)
    admin_telegram_id = Column(Integer, nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=False, index=True)
    entity_id = Column(String(100), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class StockAdjustment(Base):
    """Append-only inventory change history for admin restocks and edits."""
    __tablename__ = 'stock_adjustments'

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    adjustment_type = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    source = Column(String(50), nullable=False)
    admin_telegram_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    product = relationship("Product")


class BroadcastJob(Base):
    """Asynchronous Telegram broadcast requested by an administrator."""
    __tablename__ = 'broadcast_jobs'

    id = Column(Integer, primary_key=True)
    admin_telegram_id = Column(Integer, nullable=False)
    message_text = Column(Text, nullable=False)
    image_path = Column(String(500), nullable=True)
    status = Column(String(30), default='pending', nullable=False, index=True)
    target_count = Column(Integer, default=0, nullable=False)
    sent_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    deliveries = relationship(
        "BroadcastDelivery",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class BroadcastDelivery(Base):
    """Per-recipient delivery state used for progress and targeted retries."""
    __tablename__ = 'broadcast_deliveries'

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('broadcast_jobs.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    status = Column(String(30), default='pending', nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    error_message = Column(String(500), nullable=True)
    sent_at = Column(DateTime, nullable=True)

    job = relationship("BroadcastJob", back_populates="deliveries")
    user = relationship("User")
