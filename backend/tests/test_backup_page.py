"""Backup & Restore page: archive-name safety and page wiring."""
import unittest

from app.main import _BACKUP_NAME


class BackupNameSafetyTests(unittest.TestCase):
    def test_valid_names_pass(self):
        self.assertTrue(_BACKUP_NAME.match("atta-user-data-20260831-222212.tar.gz"))

    def test_traversal_and_junk_names_fail(self):
        for name in (
            "../etc/passwd", "atta-user-data-20260831-222212.tar.gz/../x",
            "backup.tar.gz", "atta-user-data-2026.tar.gz",
            "atta-user-data-20260831-222212.tar.gz.sh",
            "app.db", "", "atta-user-data-20260831-222212.TAR.GZ",
        ):
            with self.subTest(name=name):
                self.assertIsNone(_BACKUP_NAME.match(name))
