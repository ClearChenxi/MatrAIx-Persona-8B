"""Offline integrity/path checks; no dataset access or network requests."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import acquire_persona as acquisition


class AcquisitionTests(unittest.TestCase):
    def test_corrupted_content_is_rejected_even_at_correct_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"bad")
            entry = ("sample", 3, hashlib.sha256(b"yes").hexdigest())
            with self.assertRaises(ValueError):
                acquisition.verify(path, entry)
            self.assertEqual(path.read_bytes(), b"bad")

    def test_git_blob_hash_includes_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"yes")
            expected = hashlib.sha1(b"blob 3\0yes").hexdigest()
            result = acquisition.verify(path, ("sample", 3, expected))
            self.assertEqual(result["sha256"], hashlib.sha256(b"yes").hexdigest())

    def test_parent_traversal_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with self.assertRaises(ValueError):
                acquisition.safe_path(root, "../escape")
            (root / "linked").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                acquisition.safe_path(root, "linked/file")

    def test_frozen_download_scope(self):
        files = acquisition.FILES
        self.assertEqual(len(files), len({entry[0] for entry in files}))
        self.assertEqual(len(files), 18)
        self.assertEqual(sum(size for name, size, _ in files if name.startswith("data/")), 4167729762)
        self.assertEqual(sum(size for _, size, _ in files), 6804272774)


if __name__ == "__main__":
    unittest.main()
