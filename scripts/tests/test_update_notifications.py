"""Prevent false update notices, silent lookup failures and repeat notifications."""

import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import tempfile
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import Pin
from updates.releases import (LookupFailure, Release, archive_releases, check_pin, fetch_text,
                              find_releases, git_refs, request_json, select_updates, version_key)
from updates.notifications import Issues, marker, publish, release_marker
from updates.check import main, read_policy


def pin(version="1.2.3", url="https://github.com/owner/repo.git", commit="a" * 40):
    return Pin("fixture", version, url, "COMMIT=" + commit, "MIT", "")


class LookupTest(unittest.TestCase):
    url = "https://example.invalid/releases/"

    def test_json_lookup_recovers_after_transient_errors(self):
        errors = [urllib.error.HTTPError(self.url, 503, "unavailable", {}, None),
                  urllib.error.URLError(TimeoutError("timed out"))]
        with patch("updates.releases.urllib.request.urlopen", side_effect=errors + [io.BytesIO(b'[1]')]) as request, \
             patch("updates.releases.time.sleep") as sleep:
            self.assertEqual(request_json(self.url), [1])
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
        self.assertTrue(all(call.kwargs == {"timeout": 30} for call in request.call_args_list))

    def test_each_transient_http_status_is_retried(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status), \
                 patch("updates.releases.urllib.request.urlopen", side_effect=[
                     urllib.error.HTTPError(self.url, status, "temporary", {}, None), io.BytesIO(b"release")]) as request, \
                 patch("updates.releases.time.sleep"):
                self.assertEqual(fetch_text(self.url), "release")
                self.assertEqual(request.call_count, 2)

    def test_timeout_during_response_read_is_retried(self):
        class TimedOutResponse(io.BytesIO):
            def read(self):
                raise TimeoutError("read timed out")
        response = TimedOutResponse()
        with patch("updates.releases.urllib.request.urlopen", side_effect=[response, io.BytesIO(b"release")]) as request, \
             patch("updates.releases.time.sleep"):
            self.assertEqual(fetch_text(self.url), "release")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(response.closed)

    def test_exhausted_lookup_reports_url_attempts_and_final_error_in_issue(self):
        source = pin("9.0.2", "https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz")
        with patch("updates.releases.urllib.request.urlopen", side_effect=urllib.error.URLError(TimeoutError("timed out"))) as request, \
             patch("updates.releases.time.sleep") as sleep:
            row = check_pin(source)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(row["updates"], [])
        self.assertIn("https://ffmpeg.org/releases/", row["error"])
        self.assertIn("attempts: 3", row["error"])
        self.assertIn("URLError: <urlopen error timed out>", row["error"])
        issues = FakeIssues()
        publish([row], issues, "fixture-owner", "https://example.invalid/run")
        self.assertIn(row["error"], issues.created[0][1])
        self.assertIn("unknown update status", issues.created[0][1])

    def test_permanent_http_and_non_timeout_errors_are_not_retried(self):
        errors = [urllib.error.HTTPError(self.url, status, "permanent", {}, None) for status in (403, 404, 501)]
        errors.append(urllib.error.URLError("name resolution failed"))
        for error in errors:
            with self.subTest(error=error), \
                 patch("updates.releases.urllib.request.urlopen", side_effect=error) as request, \
                 patch("updates.releases.time.sleep") as sleep:
                with self.assertRaisesRegex(LookupFailure, "attempts: 1"):
                    fetch_text(self.url)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_invalid_json_reports_source_without_retry(self):
        with patch("updates.releases.urllib.request.urlopen", return_value=io.BytesIO(b"not JSON")) as request, \
             patch("updates.releases.time.sleep") as sleep:
            with self.assertRaises(LookupFailure) as failure:
                request_json(self.url)
        self.assertIn(self.url, str(failure.exception))
        self.assertIn("attempts: 1", str(failure.exception))
        self.assertIn("JSONDecodeError", str(failure.exception))
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_issue_writes_are_never_retried(self):
        for method in ("POST", "PATCH"):
            for error in (TimeoutError("timed out"), urllib.error.HTTPError(self.url, 503, "unavailable", {}, None)):
                with self.subTest(method=method, error=error), \
                     patch("updates.releases.urllib.request.urlopen", side_effect=error) as request, \
                     patch("updates.releases.time.sleep") as sleep:
                    with self.assertRaisesRegex(LookupFailure, "attempts: 1"):
                        request_json(self.url, method=method, data={"body": "notice"})
                    self.assertEqual(request.call_count, 1)
                    self.assertEqual(request.call_args.args[0].get_method(), method)
                    sleep.assert_not_called()

    def test_git_timeout_recovers_but_repository_errors_do_not_retry(self):
        completed = subprocess.CompletedProcess(["git"], 0, stdout="a" * 40 + "\tHEAD\n")
        with patch("updates.releases.subprocess.run", side_effect=[subprocess.TimeoutExpired(["git"], 30), completed]) as request, \
             patch("updates.releases.time.sleep"):
            self.assertEqual(git_refs(self.url, "HEAD"), [["a" * 40, "HEAD"]])
            self.assertEqual(request.call_count, 2)
        with patch("updates.releases.subprocess.run", side_effect=subprocess.CalledProcessError(128, ["git"])) as request, \
             patch("updates.releases.time.sleep") as sleep:
            with self.assertRaisesRegex(LookupFailure, "attempts: 1"):
                git_refs(self.url, "HEAD")
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()


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
                                    "ffmpeg-9.0.2.tar.xz", "9.0.2", "https://ffmpeg.org/releases/")
        self.assertEqual([row.version for row in releases], ["9.0.3", "10.0"])
        self.assertEqual(releases[0].url, "https://ffmpeg.org/releases/ffmpeg-9.0.3.tar.xz")
        with self.assertRaises(ValueError):
            archive_releases([], "ffmpeg-9.0.2.tar.xz", "9.0.2", "https://ffmpeg.org/releases/")

    def test_project_digits_do_not_become_the_archive_version(self):
        releases = archive_releases(["libbs2b-3.1.0.tar.gz", "libbs2b-3.2.0.tar.gz"],
                                    "libbs2b-3.1.0.tar.gz", "3.1.0", "https://example.invalid/")
        self.assertEqual([row.version for row in releases], ["3.1.0", "3.2.0"])

    def test_legacy_lame_filenames_are_not_version_398(self):
        releases = archive_releases(["lame-398-2.tar.gz", "lame-398.tar.gz", "lame-3.100.tar.gz"],
                                    "lame-4.0.tar.gz", "4.0", "https://example.invalid/")
        self.assertEqual(select_updates("4.0", releases), [])

    def test_rfc_tag_is_not_a_package_release(self):
        releases = [Release(version, "https://example.invalid") for version in ["webp-rfc9649", "v1.6.1"]]
        self.assertEqual([row.version for row in select_updates("v1.6.0", releases)], ["v1.6.1"])

    def test_archive_prefix_is_accepted_for_github_tags(self):
        with patch("updates.releases.find_releases", return_value=[Release("openssl-3.5.9", "https://example.invalid")]):
            result = check_pin(pin("3.5.8", "https://github.com/openssl/openssl/releases/download/openssl-3.5.8/openssl-3.5.8.tar.gz"))
        self.assertEqual(result["updates"][0]["version"], "openssl-3.5.9")

    def test_sdl2_policy_excludes_the_separate_sdl3_package(self):
        releases = [Release(version, "https://example.invalid") for version in ["release-2.32.12", "release-3.4.18"]]
        with patch("updates.releases.find_releases", return_value=releases):
            result = check_pin(pin("release-2.32.10"), {"major": 2})
        self.assertEqual([row["version"] for row in result["updates"]], ["release-2.32.12"])


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
                 patch("updates.check.read_policy", return_value={}), \
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
                 patch("updates.check.read_policy", return_value={}), \
                 patch("updates.check.check_pin", return_value=result(error="HTTP 503")[0]):
                self.assertEqual(main(), 1)
            self.assertIn("HTTP 503", report.read_text())

    def test_misspelled_policy_or_wrong_major_type_fails_explicitly(self):
        for data in ['{"missing": {"major": 2}}', '{"fixture": {"major": "2"}}']:
            with patch("pathlib.Path.read_text", return_value=data), self.assertRaises(ValueError):
                read_policy({"fixture": pin()})


if __name__ == "__main__":
    unittest.main()
