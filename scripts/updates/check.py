#!/usr/bin/env python3
"""Check pinned sources for updates; issue publication requires --notify."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import ROOT, read_pins
from updates.releases import check_pin
from updates.notifications import Issues, publish


def read_policy(pins):
    policies = json.loads(Path(__file__).with_name("release-policy.json").read_text())
    if not isinstance(policies, dict) or set(policies) - pins.keys():
        raise ValueError("release policy names must match dependency pins")
    for policy in policies.values():
        if (not isinstance(policy, dict) or set(policy) != {"major"}
                or type(policy["major"]) is not int or policy["major"] < 0):
            raise ValueError("release policy requires a nonnegative integer major")
    return policies


def summary(results):
    lines = ["## Upstream update checks", "",
             "| Component | Pinned | Available | Status |", "|---|---|---|---|"]
    for row in results:
        available = ", ".join(f"[{release['version']}]({release['url']})" for release in row["updates"]) or "—"
        status = "Check failed" if row["error"] else "Update available" if row["updates"] else "No newer version found"
        lines.append(f"| {row['name']} | {row['pinned']} | {available} | {status} |")
    failures = [row for row in results if row["error"]]
    if failures:
        lines += ["", "### Checks requiring attention", ""]
        lines += [f"- {row['name']}: {row['error']}" for row in failures]
    lines += ["", "Update notices do not determine whether a release fixes a vulnerability."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="optional component names")
    parser.add_argument("--pins", type=Path, default=ROOT / "deps.txt")
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--recipient", default=os.environ.get("NOTIFY_USER"))
    args = parser.parse_args()
    if args.notify and (args.names or not args.repository or not args.recipient):
        parser.error("--notify requires a complete inventory, --repository and --recipient")
    pins = read_pins(args.pins)
    policies = read_policy(pins)
    unknown = set(args.names) - pins.keys()
    if unknown:
        parser.error("unknown component(s): " + ", ".join(sorted(unknown)))
    selected = [pin for name, pin in pins.items() if not args.names or name in args.names]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check_pin, selected, [policies.get(pin.name, {}) for pin in selected]))
    report = summary(results)
    print(report, end="")
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(results, indent=2) + "\n")
    if args.summary:
        args.summary.write_text(report)
    if args.notify:
        run_url = (f"https://github.com/{args.repository}/actions/runs/" + os.environ["GITHUB_RUN_ID"])
        publish(results, Issues(args.repository), args.recipient, run_url)
    return int(any(row["error"] for row in results))


if __name__ == "__main__":
    sys.exit(main())
