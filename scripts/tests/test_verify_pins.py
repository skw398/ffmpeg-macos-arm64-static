"""Exercise pin failures without network access or compiling dependencies."""

import contextlib
import hashlib
import importlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import Pin
verify = importlib.import_module("verify-pins")


class VerifyPinsTest(unittest.TestCase):
    def test_cached_archive_is_checked_and_bad_redownload_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = hashlib.sha256(b"expected source").hexdigest()
            pin = Pin("fixture", "1", "https://example.invalid/source", digest, "")
            pin.archive(root).write_bytes(b"expected source")
            with patch.object(verify.urllib.request, "urlopen") as fetch:
                self.assertTrue(verify.verify_pin(pin, root, root)[0].startswith("OK"))
                fetch.assert_not_called()
            pin.archive(root).write_bytes(b"wrong cached source")
            with patch.object(verify.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong downloaded source")):
                self.assertTrue(verify.verify_pin(pin, root, root)[0].startswith("MISMATCH"))

    def test_network_failure_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pin = Pin("fixture", "1", "https://example.invalid/source", "a" * 64, "")
            temporary = pin.archive(root).with_suffix(".tarball.tmp")
            temporary.write_bytes(b"partial source")
            with patch.object(verify.urllib.request, "urlopen", side_effect=OSError("offline")), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(verify.verify_pin(pin, root, root)[0].startswith("FAIL"))
            self.assertFalse(temporary.exists())

    def test_wrong_commit_or_failed_clone_never_passes(self):
        pin = Pin("fixture", "tag", "https://example.invalid/source.git", "COMMIT=" + "a" * 40, "")
        for actual, prefix in (("b" * 40, "MISMATCH"), ("", "FAIL")):
            with self.subTest(actual=actual), patch.object(verify, "head_at", return_value=actual), \
                    patch.object(verify, "fetch_commit", side_effect=subprocess.CalledProcessError(1, "git")), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(verify.verify_pin(pin, Path("unused"), Path("unused"))[0].startswith(prefix))

    def test_command_exit_status_reflects_mismatch_and_download_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pin = Pin("fixture", "1", "https://example.invalid/source", "a" * 64, "")
            for result in (["MISMATCH fixture"], ["FAIL fixture"], ["OK fixture"]):
                with self.subTest(result=result), patch.object(sys, "argv", ["verify-pins.py", str(root)]), \
                        patch.object(verify, "env_path", return_value=root), \
                        patch.object(verify, "read_pins", return_value={pin.name: pin}), \
                        patch.object(verify, "verify_pin", return_value=result), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(verify.main(), int(not result[0].startswith("OK")))
                    self.assertIn(result[0], (root / ".verify-status").read_text())

    def test_invalid_inventory_returns_failure_before_fetching(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, "argv", ["verify-pins.py", directory]), \
                patch.object(verify, "env_path", return_value=Path(directory)), \
                patch.object(verify, "read_pins", side_effect=ValueError("duplicate pin")), \
                patch.object(verify, "verify_pin") as fetch, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify.main(), 1)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
