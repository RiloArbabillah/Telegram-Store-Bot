"""DANA SNAP-compatible client for QRIS MPM payments."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils as crypto_utils

from config.settings import settings

logger = logging.getLogger(__name__)

JAKARTA_TZ = timezone(timedelta(hours=7))

SIGNATURE_HEADERS = [
    "X-TIMESTAMP",
    "X-SIGNATURE",
    "X-PARTNER-ID",
    "X-EXTERNAL-ID",
    "CHANNEL-ID",
]


class DanaClientError(Exception):
    """Raised when a DANA API call fails."""


@dataclass(frozen=True)
class GenerateQrisResult:
    reference_no: str | None
    partner_reference_no: str
    qr_content: str | None
    qr_url: str | None
    qr_image: str | None
    redirect_url: str | None
    payload: dict


@dataclass(frozen=True)
class QueryPaymentResult:
    reference_no: str | None
    partner_reference_no: str | None
    status_code: str
    status_text: str | None
    finished_time: str | None
    payload: dict


def _minify_body(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _hash_body(body_str: str) -> str:
    return hashlib.sha256(body_str.encode("utf-8")).hexdigest().lower()


def _load_private_key(path: str):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _load_public_key(path: str):
    with open(path, "rb") as f:
        return serialization.load_pem_public_key(f.read())


def _jakarta_timestamp() -> str:
    return datetime.now(JAKARTA_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def _build_signature(timestamp: str, method: str, relative_path: str, body_str: str) -> str:
    body_hash = _hash_body(body_str)
    string_to_sign = f"{method}:{relative_path}:{body_hash}:{timestamp}"

    private_key = _load_private_key(settings.DANA_PRIVATE_KEY_PATH)

    signature = private_key.sign(
        string_to_sign.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    return base64.b64encode(signature).decode("utf-8")


def _relative_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def verify_callback_signature(headers: dict, body_bytes: bytes) -> bool:
    signature = headers.get("X-SIGNATURE") or headers.get("x-signature")
    timestamp = headers.get("X-TIMESTAMP") or headers.get("x-timestamp")

    if not signature or not timestamp:
        return False

    callback_url = settings.DANA_CALLBACK_URL
    if not callback_url:
        logger.warning("DANA callback URL is not configured; cannot verify signature")
        return False

    relative_path = _relative_path(callback_url)
    body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, bytes) else body_bytes
    body_hash = _hash_body(body_str)
    string_to_sign = f"POST:{relative_path}:{body_hash}:{timestamp}"

    try:
        public_key = _load_public_key(settings.DANA_PUBLIC_KEY_PATH)
    except FileNotFoundError:
        logger.exception("DANA public key file missing: %s", settings.DANA_PUBLIC_KEY_PATH)
        return False

    try:
        public_key.verify(
            base64.b64decode(signature),
            string_to_sign.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        logger.warning("DANA callback signature verification failed")
        return False


def generate_qris(
    *,
    partner_reference_no: str,
    amount_idr,
    merchant_id: str | None = None,
    store_id: str | None = None,
    sub_merchant_id: str | None = None,
    terminal_id: str | None = None,
    validity_period: str | None = None,
    external_id: str | None = None,
) -> GenerateQrisResult:
    if not settings.DANA_ENABLED:
        raise DanaClientError("DANA integration is disabled")

    amount_value = f"{float(amount_idr):.2f}"

    payload: dict = {
        "merchantId": merchant_id or settings.DANA_MERCHANT_ID,
        "partnerReferenceNo": partner_reference_no,
        "amount": {
            "value": amount_value,
            "currency": "IDR",
        },
        "envInfo": {
            "sourcePlatform": "TELEGRAM_BOT",
            "orderTerminalType": "SYSTEM",
            "terminalType": "SYSTEM",
        },
    }

    store = store_id or settings.DANA_STORE_ID
    if store:
        payload["storeId"] = store

    if sub_merchant_id or settings.DANA_SUB_MERCHANT_ID:
        payload["subMerchantId"] = sub_merchant_id or settings.DANA_SUB_MERCHANT_ID

    if terminal_id or settings.DANA_TERMINAL_ID:
        payload["terminalId"] = terminal_id or settings.DANA_TERMINAL_ID

    if validity_period:
        payload["validityPeriod"] = validity_period

    url = f"{settings.DANA_BASE_URL.rstrip('/')}/v1.0/qr/qr-mpm-generate.htm"
    headers = _snap_headers(method="POST", relative_path="/v1.0/qr/qr-mpm-generate.htm", payload=payload, external_id=external_id)

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=(5, 20))
    except requests.RequestException as exc:
        raise DanaClientError(f"DANA request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        raise DanaClientError(f"DANA returned non-JSON response: {response.text[:200]}")

    if response.status_code >= 400:
        raise DanaClientError(f"DANA generate QRIS failed: HTTP {response.status_code} {body}")

    if body.get("responseCode") not in (None, "200", "2000000"):
        raise DanaClientError(f"DANA returned error response: {body}")

    return GenerateQrisResult(
        reference_no=body.get("referenceNo"),
        partner_reference_no=partner_reference_no,
        qr_content=body.get("qrContent"),
        qr_url=body.get("qrUrl"),
        qr_image=body.get("qrImage"),
        redirect_url=body.get("redirectUrl"),
        payload=body,
    )


def query_payment(
    *,
    partner_reference_no: str,
    service_code: str = "47",
    merchant_id: str | None = None,
    external_id: str | None = None,
) -> QueryPaymentResult:
    if not settings.DANA_ENABLED:
        raise DanaClientError("DANA integration is disabled")

    payload = {
        "originalPartnerReferenceNo": partner_reference_no,
        "serviceCode": service_code,
        "merchantId": merchant_id or settings.DANA_MERCHANT_ID,
        "additionalInfo": {
            "envInfo": {
                "sourcePlatform": "TELEGRAM_BOT",
                "orderTerminalType": "SYSTEM",
                "terminalType": "SYSTEM",
            }
        },
    }

    url = f"{settings.DANA_BASE_URL.rstrip('/')}/rest/v1.1/debit/status"
    headers = _snap_headers(method="POST", relative_path="/rest/v1.1/debit/status", payload=payload, external_id=external_id)

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=(5, 20))
    except requests.RequestException as exc:
        raise DanaClientError(f"DANA query request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        raise DanaClientError(f"DANA query returned non-JSON response: {response.text[:200]}")

    if response.status_code >= 400:
        raise DanaClientError(f"DANA query payment failed: HTTP {response.status_code} {body}")

    return QueryPaymentResult(
        reference_no=body.get("originalReferenceNo") or body.get("referenceNo"),
        partner_reference_no=body.get("originalPartnerReferenceNo") or partner_reference_no,
        status_code=str(body.get("latestTransactionStatus") or body.get("transactionStatus") or ""),
        status_text=body.get("transactionStatusDesc") or body.get("responseMessage"),
        finished_time=body.get("finishedTime"),
        payload=body,
    )


def _snap_headers(
    *,
    method: str,
    relative_path: str,
    payload: dict,
    external_id: str | None = None,
) -> dict:
    timestamp = _jakarta_timestamp()
    body_str = _minify_body(payload)
    signature = _build_signature(timestamp=timestamp, method=method, relative_path=relative_path, body_str=body_str)

    if external_id:
        x_external_id = external_id
    else:
        x_external_id = f"{settings.DANA_PARTNER_ID}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    return {
        "Content-Type": "application/json",
        "X-TIMESTAMP": timestamp,
        "X-SIGNATURE": signature,
        "X-PARTNER-ID": settings.DANA_PARTNER_ID,
        "X-EXTERNAL-ID": x_external_id,
        "CHANNEL-ID": settings.DANA_CHANNEL_ID,
    }
