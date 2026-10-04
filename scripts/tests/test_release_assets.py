"""Regression checks for release license collection and SPDX metadata."""

import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assets = importlib.import_module("collect-release-assets")
sbom = importlib.import_module("make-sbom")
from build_data import Pin


class LicenseCollectionTest(unittest.TestCase):
    def test_git_source_with_british_license_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src/libunibreak"
            source.mkdir(parents=True)
            (source / "LICENCE").write_text("fixture license\n")
            pin = Pin("libunibreak", "fixture", "", "COMMIT=fixture", "")
            output = root / "licenses"
            assets.collect_licenses(pin, root / "src", root / "downloads", output)
            self.assertEqual((output / "LICENCE").read_text(), "fixture license\n")

    def test_tar_source_with_package_prefixed_license_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pin = Pin("leptonica", "fixture", "", "0" * 64, "")
            with tarfile.open(pin.archive(root), "w") as archive:
                content = b"fixture license\n"
                info = tarfile.TarInfo("leptonica-fixture/leptonica-license.txt")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            output = root / "licenses"
            assets.collect_licenses(pin, root / "src", root, output)
            self.assertEqual((output / "leptonica-fixture_leptonica-license.txt").read_bytes(), content)

    def test_empty_or_missing_sources_fail_even_with_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src/fixture"
            source.mkdir(parents=True)
            (source / "LICENSE").touch()
            output = root / "licenses"
            output.mkdir()
            (output / "LICENSE").write_text("stale license\n")
            for checksum in ("COMMIT=fixture", "0" * 64):
                with self.subTest(checksum=checksum):
                    pin = Pin("fixture", "fixture", "", checksum, "")
                    with self.assertRaisesRegex(ValueError, "no non-empty license files"):
                        assets.collect_licenses(pin, root / "src", root / "downloads", output)

    def test_missing_license_stops_collection_before_binary_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            pin = Pin("fixture", "fixture", "", "COMMIT=fixture", "")
            with patch.object(sys, "argv", ["collect-release-assets.py"]), \
                    patch.object(assets, "read_pins", return_value={pin.name: pin}), \
                    patch.object(assets, "env_path", return_value=Path(directory)), \
                    patch.object(assets.subprocess, "run") as run, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(assets.main(), 1)
                self.assertIn("cannot collect licenses for fixture", error.getvalue())
                run.assert_not_called()


class SbomTest(unittest.TestCase):
    def generate(self):
        with patch.object(sys, "argv", ["make-sbom.py", "--name", "same-release"]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sbom.main(), 0)
        return json.loads(output.getvalue())

    def test_license_notes_are_preserved_without_claiming_spdx_expressions(self):
        document = self.generate()
        pins = sbom.read_deps(sbom.DEPS)
        packages = {package["name"]: package for package in document["packages"]}
        self.assertEqual(set(packages), {pin["name"] for pin in pins})
        for pin in pins:
            with self.subTest(package=pin["name"]):
                package = packages[pin["name"]]
                self.assertEqual(package["licenseDeclared"], "NOASSERTION")
                self.assertEqual(package["licenseConcluded"], "NOASSERTION")
                self.assertIn(pin["license"], package["licenseComments"])
                self.assertEqual(package["versionInfo"], pin["version"])

    def test_repeated_release_name_has_unique_document_namespaces(self):
        first, second = self.generate(), self.generate()
        self.assertNotEqual(first["documentNamespace"], second["documentNamespace"])
        for document in (first, second):
            self.assertEqual(uuid.UUID(document["documentNamespace"].rsplit("/", 1)[1]).version, 4)


if __name__ == "__main__":
    unittest.main()
