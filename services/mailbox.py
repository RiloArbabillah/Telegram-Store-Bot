"""Mailbox API integration for checking purchased account emails."""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from config.settings import settings


MAILBOX_FETCH_URL = "https://chongzhi.art/api/mailbox/fetch"
DEFAULT_FOLDER = "ALL"
DEFAULT_MAX_COUNT = 10

LOGIN_HINTS = (
    "temporary chatgpt login code",
    "temporary verification code",
    "verification code",
    "login code",
    "code to continue",
)


class MailboxError(Exception):
    """Raised when the mailbox API cannot return usable data."""


@dataclass(frozen=True)
class AccountCredential:
    """Parsed purchased account credential."""

    email: str
    password: str
    line: str


def parse_account_credential(raw_value: str | None) -> AccountCredential:
    """Parse an account inventory line in the expected email----password format."""
    line = (raw_value or "").strip()
    if "----" not in line:
        raise ValueError("Account credential must use email----password format.")

    email, password = line.split("----", 1)
    email = email.strip()
    password = password.strip()

    if not email or "@" not in email or not password:
        raise ValueError("Account credential must include email and password.")

    return AccountCredential(email=email, password=password, line=f"{email}----{password}")


def mask_email(email: str) -> str:
    """Mask an email address for Telegram button labels."""
    local, separator, domain = email.partition("@")
    if not separator:
        return email[:3] + "***" if len(email) > 3 else "***"

    if len(local) <= 3:
        masked_local = local[:1] + "***"
    else:
        masked_local = local[:3] + "***" + local[-2:]

    return f"{masked_local}@{domain}"


def fetch_mailbox_messages(
    credential_line: str,
    *,
    folder: str = DEFAULT_FOLDER,
    keyword: str | None = None,
    max_count: int = DEFAULT_MAX_COUNT,
    timeout: int = 20,
) -> dict:
    """Fetch mailbox messages from the external mailbox API."""
    search_keyword = keyword or settings.MAILBOX_SEARCH_KEYWORD
    payload = {
        "line": credential_line,
        "folder": folder,
        "keyword": search_keyword,
        "max_count": max_count,
    }
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,id;q=0.8,ms;q=0.7",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": "https://chongzhi.art",
        "pragma": "no-cache",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
    }

    try:
        response = requests.post(
            MAILBOX_FETCH_URL,
            headers=headers,
            cookies={"claim_lang": "en"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise MailboxError("Mailbox request failed: timeout") from exc
    except requests.RequestException as exc:
        raise MailboxError(f"Mailbox request failed: {exc.__class__.__name__}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise MailboxError("Mailbox API returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise MailboxError("Mailbox API returned an unexpected response.")

    if data.get("ok") is not True:
        message = data.get("message") or data.get("error") or "Mailbox API returned ok=false."
        raise MailboxError(str(message))

    return data


def find_latest_login_code(mailbox_data: dict) -> tuple[dict | None, str | None]:
    """Return the newest login-code message and OTP from a mailbox response."""
    messages = mailbox_data.get("messages") or []
    if not isinstance(messages, list):
        return None, None

    for message in messages:
        if not isinstance(message, dict):
            continue

        subject = str(message.get("subject") or "")
        body = str(message.get("body") or "")
        combined = f"{subject}\n{body}".lower()

        if not any(hint in combined for hint in LOGIN_HINTS):
            continue

        otp = _extract_login_code(body)
        if not otp:
            api_otp = str(message.get("otp") or "").strip()
            if re.fullmatch(r"\d{4,8}", api_otp):
                otp = api_otp

        if otp:
            return message, otp

    return None, None


def summarize_messages(mailbox_data: dict, *, limit: int = 3) -> list[str]:
    """Build short summaries for recent mailbox messages."""
    messages = mailbox_data.get("messages") or []
    if not isinstance(messages, list):
        return []

    summaries = []
    for message in messages[:limit]:
        if not isinstance(message, dict):
            continue

        subject = str(message.get("subject") or "No subject").strip()
        sender = str(message.get("from") or "Unknown sender").strip()
        date = str(message.get("date") or "Unknown date").strip()
        otp = _extract_message_otp(message) or "-"
        summaries.append(
            f"- From: {sender}\n"
            f"  OTP: {otp}\n"
            f"  Subject: {subject}\n"
            f"  Date: {date}"
        )

    return summaries


def _extract_message_otp(message: dict) -> str | None:
    """Extract an OTP-like numeric value from one mailbox message."""
    body = str(message.get("body") or "")
    otp = _extract_login_code(body)
    if otp:
        return otp

    api_otp = str(message.get("otp") or "").strip()
    if re.fullmatch(r"\d{4,8}", api_otp):
        return api_otp

    return None


def _extract_login_code(body: str) -> str | None:
    """Extract a numeric code near login-code wording."""
    patterns = (
        r"verification code to continue:\s*(\d{4,8})",
        r"temporary verification code to continue:\s*(\d{4,8})",
        r"kode verifikasi sementara ini untuk melanjutkan:\s*(\d{4,8})",
        r"melanjutkan:\s*(\d{4,8})",
        r"login code[:\s]+(\d{4,8})",
        r"kode masuk[:\s]+(\d{4,8})",
        r"code[:\s]+(\d{4,8})",
    )
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    return None
