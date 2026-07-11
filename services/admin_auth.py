"""Authentication primitives shared by the Telegram bot and admin web panel."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from database.models import AdminOtpCode


OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _otp_hash(code: str, admin_telegram_id: int, secret: str) -> str:
    message = f"{admin_telegram_id}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _active_otp_query(session, admin_telegram_id: int, now: datetime):
    return session.query(AdminOtpCode).filter(
        AdminOtpCode.admin_telegram_id == admin_telegram_id,
        AdminOtpCode.used_at.is_(None),
        AdminOtpCode.revoked_at.is_(None),
        AdminOtpCode.expires_at >= now,
    )


def create_admin_otp(
    session,
    admin_telegram_id: int,
    *,
    secret: str,
    now: datetime | None = None,
) -> str:
    """Create an eight-digit one-time login code while persisting only its HMAC."""
    issued_at = now or _utcnow()
    code = f"{secrets.randbelow(100_000_000):08d}"

    _active_otp_query(session, admin_telegram_id, issued_at).update(
        {AdminOtpCode.revoked_at: issued_at},
        synchronize_session=False,
    )
    session.add(
        AdminOtpCode(
            code_hash=_otp_hash(code, admin_telegram_id, secret),
            admin_telegram_id=admin_telegram_id,
            created_at=issued_at,
            expires_at=issued_at + OTP_TTL,
        )
    )
    session.flush()
    return code


def consume_admin_otp(
    session,
    code: str,
    *,
    expected_admin_id: int,
    secret: str,
    now: datetime | None = None,
) -> int | None:
    """Consume a valid OTP for the configured administrator."""
    consumed_at = now or _utcnow()
    normalized_code = (code or "").strip()
    active_query = _active_otp_query(session, expected_admin_id, consumed_at)
    expected_hash = _otp_hash(normalized_code, expected_admin_id, secret)
    record = active_query.filter(AdminOtpCode.code_hash == expected_hash).first()
    if record:
        updated = session.query(AdminOtpCode).filter(
            AdminOtpCode.id == record.id,
            AdminOtpCode.used_at.is_(None),
            AdminOtpCode.revoked_at.is_(None),
        ).update({AdminOtpCode.used_at: consumed_at}, synchronize_session=False)
        if updated == 1:
            return record.admin_telegram_id
        return None

    latest = active_query.order_by(AdminOtpCode.created_at.desc(), AdminOtpCode.id.desc()).first()
    if latest:
        latest.attempt_count = int(latest.attempt_count or 0) + 1
        if latest.attempt_count >= OTP_MAX_ATTEMPTS:
            latest.revoked_at = consumed_at
    return None
