"""Authentication primitives shared by the Telegram bot and admin web panel."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from database.models import AdminLoginToken


LOGIN_TOKEN_TTL = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def build_login_url(base_url: str, raw_token: str) -> str:
    """Build a login URL whose secret fragment is never sent in HTTP requests."""
    return f"{base_url.rstrip('/')}/admin/login#{raw_token}"


def create_login_token(session, admin_telegram_id: int, *, now: datetime | None = None) -> str:
    """Create a five-minute login token while persisting only its hash."""
    issued_at = now or _utcnow()
    raw_token = secrets.token_urlsafe(32)
    session.add(
        AdminLoginToken(
            token_hash=_token_hash(raw_token),
            admin_telegram_id=admin_telegram_id,
            created_at=issued_at,
            expires_at=issued_at + LOGIN_TOKEN_TTL,
        )
    )
    session.flush()
    return raw_token


def consume_login_token(
    session,
    raw_token: str,
    *,
    expected_admin_id: int,
    now: datetime | None = None,
) -> int | None:
    """Atomically consume a valid token for the configured administrator."""
    consumed_at = now or _utcnow()
    token_hash = _token_hash(raw_token or "")
    record = session.query(AdminLoginToken).filter_by(
        token_hash=token_hash,
        admin_telegram_id=expected_admin_id,
    ).first()
    if not record or record.used_at is not None or record.expires_at < consumed_at:
        return None

    updated = session.query(AdminLoginToken).filter(
        AdminLoginToken.id == record.id,
        AdminLoginToken.used_at.is_(None),
    ).update({AdminLoginToken.used_at: consumed_at}, synchronize_session=False)
    if updated != 1:
        return None
    return record.admin_telegram_id
