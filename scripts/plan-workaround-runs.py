#!/usr/bin/env python3
"""Decide which workaround checks a run should do.

Inputs are EVENT, BEFORE, INPUT_TRIALS and INPUT_INSTALL from the environment.
Write trials and install_rules to GITHUB_OUTPUT (stdout when unset or empty),
then print the run plan. No command-line arguments are used.
"""

import argparse
import glob
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent
TRIALS = ROOT / "scripts/trial-workarounds.txt"


def words(value):
    # The shell's unquoted expansions split on IFS and expand path patterns.
    result = []
    for word in re.findall(r"[^ \t\n]+", value):
        result.extend(sorted(glob.glob(word)) or [word])
    return result


def trial_rows():
    return [line.split("|") for line in TRIALS.read_text().split("\n")]


def ids():
    return words("\n".join(
        row[0] for row in trial_rows()
        if not re.match(r"^\s*(?:#|$)", row[0])
    ))


def libs_of(trial_id):
    for row in trial_rows():
        if row[0] == trial_id:
            return row[3] if len(row) > 3 else ""
    return ""


def changed_libs():
    before = os.environ.get("BEFORE", "")
    if not before or before.startswith("0000000"):
        return set()
    diff = subprocess.run(
        ["git", "-C", str(ROOT), "diff", before, "HEAD", "--", "deps.txt"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        check=False,
    )
    return {
        line[1:].split("|", 1)[0] for line in diff.stdout.split("\n")
        if re.match(r"^[+-][a-zA-Z0-9]", line)
    }


def emit(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path or path == "/dev/stdout":
        print(f"{name}={value}", flush=True)
        return
    with open(path, "a") as output:
        output.write(f"{name}={value}\n")


def main():
    # The shell ignored command-line arguments; inputs remain environment-only.
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.parse_known_args()

    event = os.environ.get("EVENT") or "workflow_dispatch"
    selected = []
    if event == "workflow_dispatch":
        selected = words(os.environ.get("INPUT_TRIALS", "").replace(",", " "))
    elif event == "push":
        changed = changed_libs()
        for trial_id in ids():
            libs = libs_of(trial_id)
            if libs != "*" and any(lib in changed for lib in words(libs)):
                selected.append(trial_id)
    elif event == "schedule":
        selected = [trial_id for trial_id in ids() if libs_of(trial_id) == "*"]

    # Keep the shell's exact formatting, ordering and treatment of unknown ids.
    matrix = "[" + ",".join(
        '{"id":"' + trial_id + '","fast":'
        + ("false" if libs_of(trial_id) == "*" else "true") + "}"
        for trial_id in selected
    ) + "]"
    install_rules = "true" if (
        event == "schedule" or os.environ.get("INPUT_INSTALL") == "true"
    ) else "false"
    emit("trials", matrix)
    emit("install_rules", install_rules)
    print(f"== workaround run plan: trials={matrix} install_rules={install_rules}")


if __name__ == "__main__":
    main()
