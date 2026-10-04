"""Exercise release source preparation with data fixtures, never builds."""

import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
prepare = importlib.import_module("prepare-source-rebuild")


class SourceRebuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.artifacts = self.folder / "artifacts"
        self.artifacts.mkdir()
        self.archive = self.artifacts / "release-v1-sources.tar.xz"
        self.output = self.folder / "rebuild"
        self.versions = {name: "1.2.3" for name in prepare.TOOLS}
        self.versions["PYTHON_VERSION"] = "3.14.7"
        self.versions["UV_SHA256"] = "a" * 64
        action = "\n".join(f'        {name}: "{value}"' for name, value in self.versions.items())
        self.files = {"project/.github/actions/build/action.yml": action.encode(),
                      "project/.python-version": b"3.14.7\n",
                      "project/deps.txt": b"fixture|tag|https://example.invalid/source|COMMIT=" + b"b" * 40 + b"|MIT\n",
                      "upstream/fixture.tar": b"source fixture"}
        self.manifest = {"format": 1, "projectCommit": "c" * 40,
                         "packages": {"fixture": {"version": "tag", "url": "https://example.invalid/source",
                                                   "sourcePin": "COMMIT=" + "b" * 40, "path": "upstream/fixture.tar"}},
                         "files": {name: hashlib.sha256(value).hexdigest() for name, value in self.files.items()}}
        self.save()

    def save(self):
        files = self.files | {"manifest.json": json.dumps(self.manifest).encode()}
        with tarfile.open(self.archive, "w:xz") as archive:
            for name, content in files.items():
                info = tarfile.TarInfo(prepare.BUNDLE_NAME + "/" + name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        (self.artifacts / "SHA256SUMS").write_text(prepare.digest(self.archive) + "  ./" + self.archive.name + "\n")

    def test_valid_bundle_provides_its_own_project_and_tool_versions(self):
        with contextlib.redirect_stdout(io.StringIO()):
            package, versions = prepare.prepare(self.artifacts, self.output)
        self.assertEqual(package, (self.output / prepare.BUNDLE_NAME).resolve())
        self.assertEqual(versions, self.versions)
        self.assertEqual((package / "project/deps.txt").read_bytes(), self.files["project/deps.txt"])

    def test_tarball_sources_are_available_to_bundled_validation_scripts(self):
        source = self.files.pop("upstream/fixture.tar")
        checksum = hashlib.sha256(source).hexdigest()
        self.files["upstream/fixture.tarball"] = source
        self.files["project/deps.txt"] = f"fixture|1.0|https://example.invalid/source|{checksum}|MIT\n".encode()
        self.manifest["packages"]["fixture"].update(
            version="1.0", sourcePin=checksum, path="upstream/fixture.tarball")
        self.manifest["files"] = {name: hashlib.sha256(value).hexdigest()
                                  for name, value in self.files.items()}
        self.save()
        with contextlib.redirect_stdout(io.StringIO()):
            package, _ = prepare.prepare(self.artifacts, self.output)
        original = package / "upstream/fixture.tarball"
        cached = package / "project/build/downloads/fixture-1.0.tarball"
        self.assertEqual(cached.read_bytes(), source)
        self.assertEqual(prepare.digest(cached), checksum)
        self.assertEqual(original.read_bytes(), source)
        cached.write_bytes(b"modified working copy")
        self.assertEqual(original.read_bytes(), source)

    def test_outer_checksum_mismatch_and_duplicate_entry_are_rejected_before_extracting(self):
        sums = self.artifacts / "SHA256SUMS"
        original = sums.read_text()
        for value in ("0" * 64 + "  " + self.archive.name + "\n", original * 2):
            with self.subTest(value=value):
                sums.write_text(value)
                with self.assertRaisesRegex(ValueError, "source checksum"):
                    prepare.prepare(self.artifacts, self.output)
                self.assertFalse(self.output.exists())

    def test_inner_file_modification_is_rejected_even_with_a_new_outer_checksum(self):
        self.files["project/deps.txt"] += b"# changed\n"
        self.save()
        with self.assertRaisesRegex(ValueError, "manifest file inventory or checksum"):
            prepare.prepare(self.artifacts, self.output)

    def test_pin_mismatch_is_rejected(self):
        self.manifest["packages"]["fixture"]["sourcePin"] = "COMMIT=" + "d" * 40
        self.save()
        with self.assertRaisesRegex(ValueError, "source pin mismatch"):
            prepare.prepare(self.artifacts, self.output)

    def test_missing_or_ambiguous_tool_version_is_rejected(self):
        for action in (b"missing versions\n", self.files["project/.github/actions/build/action.yml"] + b'\n        XCODE_VERSION: "9.0"\n'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                self.files["project/.github/actions/build/action.yml"] = action
                self.manifest["files"]["project/.github/actions/build/action.yml"] = hashlib.sha256(action).hexdigest()
                self.save()
                with self.assertRaisesRegex(ValueError, "one pinned tool version"):
                    prepare.prepare(self.artifacts, Path(directory) / "rebuild")


if __name__ == "__main__":
    unittest.main()
