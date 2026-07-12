import unittest

from database.models import ProductType
from services.admin_operations import AdminOperationError
from services.admin_form_rules import (
    normalize_settings_form,
    parse_admin_price,
    validate_product_form,
    validate_restock_form,
)


class AdminFormRulesTests(unittest.TestCase):
    def test_price_uses_bot_amount_rule(self):
        self.assertEqual(parse_admin_price("Rp10.000"), 10000)
        self.assertEqual(parse_admin_price("10,000"), 10000)

        with self.assertRaises(AdminOperationError):
            parse_admin_price("0")
        with self.assertRaises(AdminOperationError):
            parse_admin_price("100000001")

    def test_product_form_requires_category_and_file_download_link(self):
        categories = {1}
        subcategories_by_id = {10: 1}

        with self.assertRaises(AdminOperationError):
            validate_product_form(
                {
                    "name": "Produk",
                    "description": "Desc",
                    "price": "Rp10.000",
                    "product_type": "key",
                    "category_id": "",
                    "subcategory_id": "",
                },
                category_ids=categories,
                subcategories_by_id=subcategories_by_id,
            )

        with self.assertRaises(AdminOperationError):
            validate_product_form(
                {
                    "name": "File",
                    "description": "Desc",
                    "price": "10000",
                    "product_type": "file",
                    "category_id": "1",
                    "subcategory_id": "",
                    "download_link": "",
                },
                category_ids=categories,
                subcategories_by_id=subcategories_by_id,
            )

    def test_product_form_normalizes_valid_payload(self):
        result = validate_product_form(
            {
                "name": " Produk ",
                "description": " Desc ",
                "price": "Rp25.000",
                "product_type": "key",
                "category_id": "1",
                "subcategory_id": "10",
                "is_active": "1",
            },
            category_ids={1},
            subcategories_by_id={10: 1},
        )

        self.assertEqual(result.name, "Produk")
        self.assertEqual(result.description, "Desc")
        self.assertEqual(result.price, 25000)
        self.assertEqual(result.product_type, ProductType.KEY)
        self.assertEqual(result.category_id, 1)
        self.assertEqual(result.subcategory_id, 10)
        self.assertTrue(result.is_active)

    def test_restock_akun_accepts_exactly_one_credential(self):
        with self.assertRaises(AdminOperationError):
            validate_restock_form(ProductType.AKUN, "akun1\nakun2")

        self.assertEqual(validate_restock_form(ProductType.AKUN, "akun1"), ["akun1"])
        self.assertEqual(validate_restock_form(ProductType.KEY, "A\n\nB"), ["A", "B"])

    def test_settings_usernames_are_saved_without_at(self):
        result = normalize_settings_form(
            {
                "welcome_message": " Halo ",
                "support_username": "@support",
                "channel_username": " @channel ",
                "qris_instructions_text": " Scan QR ",
                "qris_static_payload": " payload ",
            }
        )

        self.assertEqual(result.welcome_message, "Halo")
        self.assertEqual(result.support_username, "support")
        self.assertEqual(result.channel_username, "channel")
        self.assertEqual(result.qris_instructions_text, "Scan QR")
        self.assertEqual(result.qris_static_payload, "payload")


if __name__ == "__main__":
    unittest.main()
