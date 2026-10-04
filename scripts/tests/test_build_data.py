"""Reject incomplete dependency inventories before they reach the build."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import archive_files, read_pins, read_recipes


class BuildDataTest(unittest.TestCase):
    def read(self, text, reader=read_pins):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.txt"
            path.write_text(text)
            return reader(path)

    def test_comments_whitespace_and_optional_notes(self):
        pins = self.read("\n # comment\n fixture | 1 | https://example.invalid/source | " + "a" * 64 + " | MIT\n")
        self.assertEqual(pins["fixture"].version, "1")
        self.assertEqual(pins["fixture"].notes, "")

    def test_empty_duplicate_and_malformed_pins_are_rejected(self):
        row = "fixture|1|https://example.invalid/source|" + "a" * 64 + "|MIT\n"
        cases = ["# comments only\n", row + row, "fixture|1\n",
                 row.replace("fixture|1|", "fixture||"), row.replace("a" * 64, "not-a-hash"),
                 row.replace("a" * 64, "COMMIT="), row.rstrip() + "|notes|extra\n"]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)

    def test_recipe_empty_flags_do_not_shift_subdirectory(self):
        recipe = self.read("fixture|cmake||reason|src\n", read_recipes)[0]
        self.assertEqual((recipe.flags, recipe.note, recipe.subdir), ("", "reason", "src"))

    def test_corrupt_source_archive_is_not_silently_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.tarball"
            path.write_bytes(b"not an archive")
            import tarfile
            with self.assertRaises(tarfile.TarError):
                list(archive_files(path))


if __name__ == "__main__":
    unittest.main()
