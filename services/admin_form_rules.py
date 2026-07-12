"""Shared admin form validation rules for Telegram and web admin surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from database.models import ProductType
from services.admin_operations import AdminOperationError
from utils import parse_keys_from_text, validate_amount


@dataclass(frozen=True)
class ProductFormData:
    name: str
    description: str | None
    price: int
    product_type: ProductType
    category_id: int
    subcategory_id: int | None
    download_link: str | None
    is_active: bool


@dataclass(frozen=True)
class SettingsFormData:
    welcome_message: str
    support_username: str | None
    channel_username: str | None
    qris_instructions_text: str | None
    qris_static_payload: str | None


def _get(form: Mapping[str, object], key: str, default: str = "") -> str:
    value = form.get(key, default)
    return str(value if value is not None else default)


def parse_admin_price(raw_value: str) -> int:
    is_valid, amount, error_message = validate_amount(raw_value)
    if not is_valid:
        raise AdminOperationError(error_message)
    return amount


def _parse_required_int(raw_value: str, *, label: str) -> int:
    value = raw_value.strip()
    if not value:
        raise AdminOperationError(f"{label} wajib dipilih.")
    try:
        return int(value)
    except ValueError as exc:
        raise AdminOperationError(f"{label} tidak valid.") from exc


def validate_product_form(
    form: Mapping[str, object],
    *,
    category_ids: set[int],
    subcategories_by_id: dict[int, int | None],
) -> ProductFormData:
    name = _get(form, "name").strip()
    if not name:
        raise AdminOperationError("Nama produk wajib diisi.")

    price = parse_admin_price(_get(form, "price"))

    try:
        product_type = ProductType(_get(form, "product_type").strip())
    except ValueError as exc:
        raise AdminOperationError("Tipe produk tidak valid.") from exc

    category_id = _parse_required_int(_get(form, "category_id"), label="Kategori")
    if category_id not in category_ids:
        raise AdminOperationError("Kategori tidak valid.")

    subcategory_id = None
    raw_subcategory_id = _get(form, "subcategory_id").strip()
    if raw_subcategory_id:
        try:
            subcategory_id = int(raw_subcategory_id)
        except ValueError as exc:
            raise AdminOperationError("Subkategori tidak valid.") from exc
        parent_id = subcategories_by_id.get(subcategory_id)
        if parent_id is None:
            raise AdminOperationError("Subkategori tidak valid.")
        if parent_id != category_id:
            raise AdminOperationError("Subkategori harus sesuai dengan kategori produk.")

    download_link = _get(form, "download_link").strip() or None
    if product_type == ProductType.FILE and not download_link:
        raise AdminOperationError("Link unduhan wajib diisi untuk produk file.")

    return ProductFormData(
        name=name,
        description=_get(form, "description").strip() or None,
        price=price,
        product_type=product_type,
        category_id=category_id,
        subcategory_id=subcategory_id,
        download_link=download_link,
        is_active=_get(form, "is_active") == "1",
    )


def validate_restock_form(product_type: ProductType, raw_items: str) -> list[str]:
    items = parse_keys_from_text(raw_items)
    if not items:
        raise AdminOperationError("Masukkan minimal satu item stok.")
    if product_type == ProductType.AKUN and len(items) != 1:
        raise AdminOperationError("Restock AKUN hanya menerima 1 credential per submit.")
    return items


def _normalize_username(raw_value: str) -> str | None:
    return raw_value.strip().replace("@", "") or None


def normalize_settings_form(form: Mapping[str, object]) -> SettingsFormData:
    return SettingsFormData(
        welcome_message=_get(form, "welcome_message").strip(),
        support_username=_normalize_username(_get(form, "support_username")),
        channel_username=_normalize_username(_get(form, "channel_username")),
        qris_instructions_text=_get(form, "qris_instructions_text").strip() or None,
        qris_static_payload=_get(form, "qris_static_payload").strip() or None,
    )
