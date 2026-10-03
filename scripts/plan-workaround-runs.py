#!/usr/bin/env python3
"""Decide which workaround checks a run should do.

Inputs are EVENT, BEFORE, INPUT_TRIALS and INPUT_INSTALL from the environment.
Write trials and install_rules to GITHUB_OUTPUT (stdout when unset or empty),
then print the run plan. No command-line arguments are used.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent
TRIALS = ROOT / "scripts/trial-workarounds.txt"


def read_trials():
    trials = {}
    for line in TRIALS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            trial_id, section, description, libs = line.split("|")
            trials[trial_id] = libs.split()
    return trials


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
        line[1:].split("|", 1)[0] for line in diff.stdout.splitlines()
        if re.match(r"^[+-][a-zA-Z0-9]", line)
    }


def emit(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}", flush=True)
        return
    with open(path, "a") as output:
        output.write(f"{name}={value}\n")


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    trials = read_trials()
    event = os.environ.get("EVENT") or "workflow_dispatch"
    selected = []
    if event == "workflow_dispatch":
        selected = os.environ.get("INPUT_TRIALS", "").replace(",", " ").split()
    elif event == "push":
        changed = changed_libs()
        selected = [trial_id for trial_id, libs in trials.items()
                    if "*" not in libs and changed.intersection(libs)]
    elif event == "schedule":
        selected = [trial_id for trial_id, libs in trials.items() if "*" in libs]

    matrix = json.dumps([
        {"id": trial_id, "fast": "*" not in trials.get(trial_id, [])}
        for trial_id in selected
    ], separators=(",", ":"))
    install_rules = "true" if (
        event == "schedule" or os.environ.get("INPUT_INSTALL") == "true"
    ) else "false"
    emit("trials", matrix)
    emit("install_rules", install_rules)
    print(f"== workaround run plan: trials={matrix} install_rules={install_rules}")


if __name__ == "__main__":
    main()
