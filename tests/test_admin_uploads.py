import io
import os
import tempfile
import unittest

from werkzeug.datastructures import FileStorage

from services.admin_uploads import UploadError, save_document, save_image


class AdminUploadTests(unittest.TestCase):
    def test_image_is_validated_and_saved_with_random_name(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        buffer.seek(0)
        upload = FileStorage(stream=buffer, filename="../../logo.png", content_type="image/png")

        with tempfile.TemporaryDirectory() as root:
            path = save_image(upload, root, "logos")

            self.assertTrue(path.startswith(os.path.join(root, "logos")))
            self.assertNotIn("..", path)
            self.assertTrue(os.path.exists(path))

    def test_rejects_disallowed_or_oversized_document(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(UploadError):
                save_document(FileStorage(stream=io.BytesIO(b"x"), filename="payload.exe"), root, "support")
            with self.assertRaises(UploadError):
                save_document(
                    FileStorage(stream=io.BytesIO(b"x" * 20), filename="notes.txt"),
                    root,
                    "support",
                    max_bytes=10,
                )


if __name__ == "__main__":
    unittest.main()
