"""Filesystem adapter has the same opaque-key contract as the private S3 adapter."""
import unittest
import support  # configures isolated test storage before app imports
from app.storage import LocalStorage


class StorageBoundaryTests(unittest.TestCase):
    def test_object_keys_cannot_escape_storage_root(self):
        storage = LocalStorage()
        for key in ("../secret", "../../etc/passwd", "/etc/passwd"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    storage.path(key)
