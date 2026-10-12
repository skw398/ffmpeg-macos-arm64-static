"""Prevent false update notices, silent lookup failures and repeat notifications."""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import Pin
from updates.releases import Release, archive_releases, check_pin, find_releases, select_updates, version_key
from updates.notifications import Issues, marker, publish, release_marker
from updates.check import main


def pin(version="1.2.3", url="https://github.com/owner/repo.git", commit="a" * 40):
    return Pin("fixture", version, url, "COMMIT=" + commit, "MIT", "")


class DetectionTest(unittest.TestCase):
    def test_tag_formats_and_archive_suffixes(self):
        for first, second in [("v1.2.3", "1.2.3"), ("release-3.0.6", "3.0.6"),
                              ("libssh-0.11.5", "0.11.5"), ("libunibreak_8_0", "8.0"),
                              ("lcms2.19.1", "2.19.1"), ("0.8.9+release.autotools", "0.8.9"),
                              ("1.2", "1.2.0")]:
            self.assertEqual(version_key(first), version_key(second))

    def test_active_series_is_not_hidden_by_new_major_release(self):
        releases = [Release(version, "https://example.invalid/" + version)
                    for version in ["9.0.2", "9.0.3", "9.0.4", "9.1.0", "10.0.0", "10.1.0rc1"]]
        self.assertEqual([row.version for row in select_updates("9.0.2", releases)], ["9.0.4", "10.0.0"])

    def test_equal_older_aliases_and_preview_tags_do_not_notify(self):
        releases = [Release(version, "https://example.invalid")
                    for version in ["1.1.0", "v1.2.3", "1.2.3.0", "2.0.0rc1", "2.0.0.beta1", "3gpp-2021-10-15-5"]]
        self.assertEqual(select_updates("1.2.3", releases), [])

    def test_already_pinned_beta_series(self):
        releases = [Release(version, "https://example.invalid") for version in ["0.99.beta21", "1.0.beta1"]]
        self.assertEqual([row.version for row in select_updates("0.99.beta20", releases)], ["0.99.beta21"])

    def test_unknown_pin_or_source_versions_are_reported_as_failures(self):
        with patch("updates.releases.find_releases", return_value=[Release("1.3", "https://example.invalid")]):
            self.assertIsNotNone(check_pin(pin("unknown-version"))["error"])
        with patch("updates.releases.find_releases", return_value=[Release("nightly", "https://example.invalid")]):
            self.assertIsNotNone(check_pin(pin())["error"])

    def test_git_and_http_failures_are_not_no_update(self):
        for error in [OSError("lookup failed"), subprocess.CalledProcessError(128, ["git"]),
                      subprocess.TimeoutExpired(["git"], 30)]:
            with patch("updates.releases.find_releases", side_effect=error):
                result = check_pin(pin())
                self.assertTrue(result["error"])
                self.assertEqual(result["updates"], [])

    def test_commit_pins_compare_full_commit_not_shortened_head(self):
        current = "a" * 40
        for latest, expected in [(current, 0), ("a" * 12 + "b" * 28, 1)]:
            with patch("updates.releases.git_refs", return_value=[[latest, "HEAD"]]):
                result = check_pin(pin(current, "https://code.videolan.org/videolan/x264.git", current))
                self.assertIsNone(result["error"])
                self.assertEqual(len(result["updates"]), expected)

    def test_draft_and_prerelease_github_releases_are_excluded(self):
        records = [{"tag_name": version, "html_url": "https://example.invalid", "draft": draft, "prerelease": preview}
                   for version, draft, preview in [("2.0.0", False, True), ("1.3.0", True, False), ("1.2.4", False, False)]]
        with patch("updates.releases.request_json", return_value=records):
            self.assertEqual([row.version for row in find_releases(pin())], ["1.2.4"])

    def test_tag_fallback_for_namespaced_tags(self):
        with patch("updates.releases.request_json", side_effect=[[], [{"name": "libunibreak_9_0"}]]):
            result = check_pin(pin("libunibreak_8_0"))
        self.assertIsNone(result["error"])
        self.assertEqual(result["updates"][0]["version"], "libunibreak_9_0")

    def test_archive_listing_ignores_other_projects_and_extensions(self):
        releases = archive_releases(["ffmpeg-9.0.3.tar.xz", "ffmpeg-10.0.tar.xz",
                                     "ffmpeg-99.0.tar.gz", "other-99.0.tar.xz"],
                                    "ffmpeg-9.0.2.tar.xz", "https://ffmpeg.org/releases/")
        self.assertEqual([row.version for row in releases], ["9.0.3", "10.0"])
        self.assertEqual(releases[0].url, "https://ffmpeg.org/releases/ffmpeg-9.0.3.tar.xz")
        with self.assertRaises(ValueError):
            archive_releases([], "ffmpeg-9.0.2.tar.xz", "https://ffmpeg.org/releases/")


class FakeIssues:
    def __init__(self, records=()):
        self.records = list(records)
        self.created = []
        self.changed = []

    def list(self):
        return list(self.records)

    def create(self, title, body):
        record = {"number": len(self.records) + 1, "state": "open", "body": body,
                  "html_url": "https://example.invalid/issue"}
        self.records.append(record)
        self.created.append((title, body))
        return record

    def update(self, number, **fields):
        self.changed.append((number, fields))
        next(row for row in self.records if row["number"] == number).update(fields)


def result(*versions, error=None):
    return [{"name": "fixture", "pinned": "1.2.3", "error": error,
             "updates": [{"version": version, "url": "https://example.invalid/release"} for version in versions]}]


class NotificationTest(unittest.TestCase):
    def publish(self, results, issues):
        publish(results, issues, "fixture-owner", "https://example.invalid/run")

    def test_closed_issue_prevents_repeat_notice_and_alias_duplicate(self):
        issues = FakeIssues([{"number": 7, "state": "closed", "body": release_marker("fixture", "v1.2.4")}])
        self.publish(result("1.2.4"), issues)
        self.assertEqual(issues.created, [])
        self.publish(result("1.2.5"), issues)
        self.assertEqual(len(issues.created), 1)

    def test_repeat_runs_and_duplicate_candidates_notify_once(self):
        issues = FakeIssues()
        self.publish(result("1.2.4", "v1.2.4"), issues)
        self.publish(result("1.2.4"), issues)
        self.assertEqual(len(issues.created), 1)

    def test_failure_deduplication_recovery_and_reopening(self):
        issues = FakeIssues()
        self.publish(result(error="HTTP 503"), issues)
        self.publish(result(error="HTTP 503"), issues)
        self.assertEqual(len(issues.created), 1)
        self.assertEqual(issues.changed, [])
        self.publish(result(), issues)
        self.assertEqual(issues.changed[-1][1], {"state": "closed"})
        self.publish(result(error="HTTP 503"), issues)
        self.assertEqual(len(issues.created), 1)
        self.assertEqual(issues.changed[-1][1]["state"], "open")

    def test_partial_failure_still_publishes_successful_update(self):
        issues = FakeIssues()
        rows = result("1.2.4") + [{"name": "unavailable", "pinned": "1", "updates": [], "error": "timeout"}]
        self.publish(rows, issues)
        self.assertEqual(len(issues.created), 2)
        self.assertIn("@fixture-owner", issues.created[0][1])

    def test_issue_pagination_keeps_closed_notices_and_excludes_prs(self):
        first_page = [{"number": number, "state": "open", "body": "", "pull_request": {}} for number in range(100)]
        closed = {"number": 101, "state": "closed", "body": marker("release", "old")}
        with patch("updates.notifications.request_json", side_effect=[first_page, [closed]]) as request:
            rows = Issues("owner/repo").list()
        self.assertEqual(rows, [closed])
        self.assertEqual(request.call_count, 2)

    def test_default_run_writes_report_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch("sys.argv", ["check.py", "--json", str(report)]), \
                 patch("updates.check.read_pins", return_value={"fixture": pin()}), \
                 patch("updates.check.check_pin", return_value=result("1.2.4")[0]), \
                 patch("updates.check.publish") as publisher:
                self.assertEqual(main(), 0)
            publisher.assert_not_called()
            self.assertIn("1.2.4", report.read_text())

    def test_lookup_failure_is_a_nonzero_exit_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch("sys.argv", ["check.py", "--json", str(report)]), \
                 patch("updates.check.read_pins", return_value={"fixture": pin()}), \
                 patch("updates.check.check_pin", return_value=result(error="HTTP 503")[0]):
                self.assertEqual(main(), 1)
            self.assertIn("HTTP 503", report.read_text())


if __name__ == "__main__":
    unittest.main()
