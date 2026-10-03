"""Regression tests for pkg-config requirement fields."""

import importlib
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
requirements = importlib.import_module("report-ambient-deps").requirements


class RequirementsTest(unittest.TestCase):
    def test_empty_fields_do_not_consume_following_lines(self):
        text = """Name: fixture
Requires:
Libs: -L${libdir} -lfixture
Requires.private:
Cflags: -I${includedir} -DFIXTURE
Conflicts: another
"""
        self.assertEqual(requirements(text), set())

    def test_empty_public_field_keeps_private_dependencies(self):
        text = "Requires:\nRequires.private: libalpha >= 1.0, libbeta\nLibs: -lfixture\n"
        self.assertEqual(requirements(text), {"libalpha", "libbeta"})

    def test_constraints_and_separators(self):
        text = "Requires: alpha>=1.0, beta <= 2 gamma = 3\nRequires.private: delta\n"
        self.assertEqual(requirements(text), {"alpha", "beta", "gamma", "delta"})


if __name__ == "__main__":
    unittest.main()
