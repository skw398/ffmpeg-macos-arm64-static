"""Publish release notices once; closed issues also count as already notified."""

import hashlib
import json
import re

from updates.releases import request_json, version_key


def marker(kind, identity=""):
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"<!-- upstream-updates:{kind}:{digest} -->"


def release_marker(name, version):
    return marker("release", name + ":" + json.dumps(version_key(version) or version))


class Issues:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid GitHub repository")
        self.base = f"https://api.github.com/repos/{repository}/issues"

    def list(self):
        rows = []
        page = 1
        while True:
            batch = request_json(f"{self.base}?state=all&per_page=100&page={page}")
            rows.extend(row for row in batch if "pull_request" not in row)
            if len(batch) < 100:
                return rows
            page += 1

    def create(self, title, body):
        return request_json(self.base, method="POST", data={"title": title, "body": body})

    def update(self, number, **fields):
        return request_json(f"{self.base}/{number}", method="PATCH", data=fields)


def publish(results, issues, recipient, run_url):
    if not re.fullmatch(r"[A-Za-z0-9-]+", recipient):
        raise ValueError("invalid notification recipient")
    existing = issues.list()
    for row in results:
        for release in row["updates"]:
            stamp = release_marker(row["name"], release["version"])
            if any(stamp in (issue.get("body") or "") for issue in existing):
                continue
            body = (f"@{recipient}\n\n"
                    f"- Component: **{row['name']}**\n"
                    f"- Pinned version: `{row['pinned']}`\n"
                    f"- Upstream version: `{release['version']}`\n"
                    f"- [Upstream release/source]({release['url']})\n\n"
                    "Review release notes, compatibility and security fixes before updating. "
                    "This is an update notice, not a vulnerability assessment.\n\n"
                    f"[Check run]({run_url})\n\n{stamp}\n")
            created = issues.create(f"Update available: {row['name']} {release['version']}", body)
            existing.append(created)
            print("Created update notice:", created["html_url"])
    errors = [{"name": row["name"], "error": row["error"]} for row in results if row["error"]]
    failure_stamp = marker("failures")
    failure = next((issue for issue in existing if failure_stamp in (issue.get("body") or "")), None)
    if not errors:
        if failure and failure["state"] == "open":
            issues.update(failure["number"], state="closed")
        return
    fingerprint = marker("failure-state", json.dumps(errors, sort_keys=True))
    body = (f"@{recipient}\n\nThe following upstream checks could not complete. "
            "These components have an unknown update status, not a confirmed absence of updates.\n\n"
            + "\n".join(f"- **{row['name']}**: {row['error']}" for row in errors)
            + f"\n\n[Check run]({run_url})\n\n{failure_stamp}\n{fingerprint}\n")
    if failure:
        if failure["state"] != "open" or fingerprint not in (failure.get("body") or ""):
            issues.update(failure["number"], state="open", body=body)
    else:
        issues.create("Upstream update checks could not complete", body)
