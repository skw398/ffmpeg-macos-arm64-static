"""Check missing and disabled consumers with text fixtures only."""

import contextlib
import importlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
consumers = importlib.import_module("lib-consumers")
CONFIGURE = '''EXTERNAL_LIBRARY_LIST="
fixture optional unused
"
fixture_decoder_deps="fixture"
optional_filter_suggest="optional"
'''


class ConsumersTest(unittest.TestCase):
    def test_disabled_missing_and_unknown_consumers_fail(self):
        cases = [("fixture", "fixture_decoder", 1), ("unused", "", 1),
                 ("unknown", "", 1), ("fixture", "", 0), ("optional", "", 0)]
        for library, disabled, status in cases:
            with self.subTest(library=library, disabled=disabled), \
                    patch.object(sys, "argv", ["lib-consumers.py", "--check", "--disabled", disabled, library]), \
                    patch.object(consumers, "configure_text", return_value=CONFIGURE), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(consumers.main(), status)

    def test_zero_or_missing_config_macros_are_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / "config.h"
            header.write_text("#define CONFIG_FIXTURE_DECODER 0\n#define CONFIG_OPTIONAL_FILTER 1\n")
            check = consumers.ConsumerCheck(headers=[header])
            self.assertFalse(check.enabled("fixture_decoder"))
            self.assertFalse(check.enabled("missing_encoder"))
            self.assertTrue(check.enabled("optional_filter"))

    def test_binary_listing_headers_do_not_count_as_components(self):
        self.assertEqual(consumers.list_names("Decoders:\n V..... fixture Fixture decoder\n A..... other Other decoder\n"),
                         {"fixture", "other"})

    def test_binary_command_failure_propagates_as_error(self):
        with patch.object(sys, "argv", ["lib-consumers.py", "--check", "--binary", "fixture-binary", "fixture"]), \
                patch.object(consumers.os, "access", return_value=True), \
                patch.object(consumers, "configure_text", return_value=CONFIGURE), \
                patch.object(consumers.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "fixture-binary")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(consumers.main(), 2)


if __name__ == "__main__":
    unittest.main()
