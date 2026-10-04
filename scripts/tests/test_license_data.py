"""License reviews must follow source updates and retain custom declarations."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import DATA, license_identifiers, read_licenses, read_pins


class LicenseDataTest(unittest.TestCase):
    def setUp(self):
        self.pins = read_pins()
        self.catalog = json.loads((DATA / "licenses.json").read_text())

    def read(self, catalog, pins=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "licenses.json"
            path.write_text(json.dumps(catalog))
            return read_licenses(self.pins if pins is None else pins, path)

    def test_source_updates_require_a_new_review(self):
        for field, value in (("version", "new-version"), ("checksum", "0" * 64), ("license", "MIT")):
            with self.subTest(field=field):
                pins = self.pins | {"ffmpeg": replace(self.pins["ffmpeg"], **{field: value})}
                with self.assertRaisesRegex(ValueError, "does not match source pin"):
                    self.read(self.catalog, pins)

    def test_incomplete_inventory_evidence_and_license_refs_are_rejected(self):
        cases = [
            (lambda data: data["packages"].pop("ffmpeg"), "license inventory"),
            (lambda data: data["extractedLicensingInfo"].pop(), "unknown license identifier"),
            (lambda data: data["extractedLicensingInfo"][0].update(extractedText=""), "invalid extracted license"),
            (lambda data: data["packages"]["ffmpeg"].update(licenseFiles=[]), "missing license files"),
            (lambda data: data["packages"]["ffmpeg"]["evidence"].clear(), "missing license file evidence"),
            (lambda data: data["packages"]["ffmpeg"]["evidence"].update({"../COPYING": "a" * 64}), "invalid license evidence"),
            (lambda data: data["packages"]["libaribb24"].update(comment="unknown"), "missing unresolved license reason"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                data = copy.deepcopy(self.catalog)
                change(data)
                with self.assertRaisesRegex(ValueError, message):
                    self.read(data)

    def test_expressions_require_known_identifiers_and_complete_terms(self):
        known = {"MIT", "BSD-2-Clause", "GPL-2.0-or-later", "LicenseRef-custom"}
        self.assertEqual(license_identifiers("MIT AND (BSD-2-Clause OR LicenseRef-custom)", known),
                         {"MIT", "BSD-2-Clause", "LicenseRef-custom"})
        for expression in ("MIT AND", "(MIT", "MIT BSD-2-Clause", "MIT OR unknown", "MIT, BSD-2-Clause", ""):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                license_identifiers(expression, known)


if __name__ == "__main__":
    unittest.main()
