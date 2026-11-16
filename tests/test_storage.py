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


class S3MissingObjectTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from app.storage import S3Storage
        self.storage = S3Storage.__new__(S3Storage)
        self.storage.client = Mock()

    def test_missing_keys_match_filesystem_contract(self):
        from botocore.exceptions import ClientError
        for operation, call in (
            ('head_object', lambda: self.storage.size('missing')),
            ('get_object', lambda: self.storage.read('missing', 0, 10)),
            ('download_file', lambda: self.storage.download('missing', '/unused')),
        ):
            for code in ('404', 'NoSuchKey', 'NotFound'):
                with self.subTest(operation=operation, code=code):
                    getattr(self.storage.client, operation).side_effect = ClientError({'Error': {'Code': code}}, operation)
                    with self.assertRaises(FileNotFoundError):
                        call()

    def test_s3_access_denied_is_not_misreported_as_missing(self):
        from botocore.exceptions import ClientError
        self.storage.client.head_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'HeadObject')
        with self.assertRaises(ClientError):
            self.storage.size('private')
