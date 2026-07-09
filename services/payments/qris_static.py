"""Helpers for decoded static QRIS payloads and dynamic amount rendering."""

from __future__ import annotations

from io import BytesIO


class QrisPayloadError(ValueError):
    """Raised when a QRIS payload cannot be decoded or rebuilt."""


def decode_qr_payload_from_image(image_bytes: bytes) -> str:
    """Decode the first QR payload from image bytes."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise QrisPayloadError("QR decoder dependency is not installed.") from exc

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise QrisPayloadError("Uploaded image could not be read.")

    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(image)
    payload = (data or "").strip()
    if not payload:
        raise QrisPayloadError("No QR payload found in the uploaded image.")

    parse_tlv(payload)
    return payload


def parse_tlv(payload: str) -> list[tuple[str, str]]:
    """Parse EMV-style TLV into ordered tag/value tuples."""
    items: list[tuple[str, str]] = []
    pos = 0
    length = len(payload)

    while pos < length:
        if pos + 4 > length:
            raise QrisPayloadError("Invalid QRIS payload length.")

        tag = payload[pos:pos + 2]
        raw_len = payload[pos + 2:pos + 4]
        if not tag.isdigit() or not raw_len.isdigit():
            raise QrisPayloadError("Invalid QRIS TLV tag or length.")

        value_len = int(raw_len)
        value_start = pos + 4
        value_end = value_start + value_len
        if value_end > length:
            raise QrisPayloadError("Invalid QRIS TLV value length.")

        items.append((tag, payload[value_start:value_end]))
        pos = value_end

    if not items:
        raise QrisPayloadError("QRIS payload is empty.")

    return items


def generate_dynamic_qris(static_payload: str, amount_idr: int) -> str:
    """Return a QRIS payload with a fixed transaction amount and fresh CRC."""
    if amount_idr <= 0:
        raise QrisPayloadError("QRIS amount must be greater than zero.")

    items = [(tag, value) for tag, value in parse_tlv(static_payload) if tag not in {"54", "63"}]
    values_by_tag = {tag: value for tag, value in items}
    values_by_tag["01"] = "12"
    values_by_tag["54"] = str(int(amount_idr))

    encoded = "".join(
        _encode_tlv(tag, values_by_tag[tag])
        for tag in sorted(values_by_tag)
    )
    crc = _crc16_ccitt((encoded + "6304").encode("ascii"))
    return f"{encoded}6304{crc:04X}"


def render_qris_png_bytes(payload: str) -> bytes:
    """Render a QR payload to PNG bytes ready for Telegram upload."""
    try:
        import qrcode
    except ImportError as exc:
        raise QrisPayloadError("QR renderer dependency is not installed.") from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=12,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _encode_tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise QrisPayloadError(f"QRIS tag {tag} is too long.")
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF
