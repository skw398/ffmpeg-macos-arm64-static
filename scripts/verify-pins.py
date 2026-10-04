#!/usr/bin/env python3
"""Verify dependency archive SHA-256 pins and git commit pins."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

from build_data import ROOT, env_path, read_pins


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def head_at(path):
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def fetch_commit(pin, destination):
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    if re.fullmatch(r"[0-9a-f]{40}", pin.version):
        destination.mkdir(parents=True)
        def git(*args):
            subprocess.run(["git", "-C", str(destination), *args], check=True)
        git("init", "-q")
        git("remote", "add", "origin", pin.url)
        git("fetch", "-q", "--depth", "1", "origin", pin.version)
        git("checkout", "-q", "FETCH_HEAD")
    else:
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch",
                        pin.version, pin.url, str(destination)], check=True)


def verify_pin(pin, downloads, sources):
    if pin.checksum in {"HASH-TODO", "PIN-IN-CI"}:
        return [f"SKIP  {pin.name} (hash field: {pin.checksum})"]
    if pin.commit:
        destination = sources / pin.name
        actual = head_at(destination)
        if actual != pin.commit:
            try:
                fetch_commit(pin, destination)
            except (OSError, subprocess.CalledProcessError) as error:
                print(f"ERROR: cannot fetch {pin.name}: {error}", file=sys.stderr)
            actual = head_at(destination)
        if actual == pin.commit:
            return [f"OK    {pin.name}-{pin.version} (git HEAD={pin.commit})"]
        if actual:
            return [f"MISMATCH  {pin.name}-{pin.version} (git)",
                    f"          expected: {pin.commit}", f"          actual:   {actual}"]
        return [f"FAIL  {pin.name}  (git clone failed: {pin.url} ref {pin.version})"]

    archive = pin.archive(downloads)
    actual = sha256(archive) if archive.is_file() else ""
    if actual != pin.checksum:
        temporary = archive.with_suffix(archive.suffix + ".tmp")
        try:
            with urllib.request.urlopen(pin.url, timeout=300) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            temporary.replace(archive)
            actual = sha256(archive)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            print(f"ERROR: cannot download {pin.name}: {error}", file=sys.stderr)
            return [f"FAIL  {pin.name}  (download failed: {pin.url})"]
    if actual == pin.checksum:
        return [f"OK    {pin.name}-{pin.version}"]
    return [f"MISMATCH  {pin.name}-{pin.version}",
            f"          expected: {pin.checksum}", f"          actual:   {actual}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("downloads", nargs="?", type=Path, default=ROOT / "build/downloads")
    parser.add_argument("--skip", action="store_true", help="iteration only; never for release runs")
    args = parser.parse_args()
    if args.skip:
        print("SKIP: pin verification disabled (--skip)")
        return 0
    sources = env_path("SRC", ROOT / "build/src")
    args.downloads.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    try:
        pins = read_pins()
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    lines = []
    for pin in pins.values():
        try:
            lines.extend(verify_pin(pin, args.downloads, sources))
        except OSError as error:
            lines.append(f"FAIL  {pin.name}  ({error})")
    (args.downloads / ".verify-status").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    failed = sum(line.startswith(("FAIL", "MISMATCH")) for line in lines)
    skipped = sum(line.startswith("SKIP") for line in lines)
    ok = sum(line.startswith("OK") for line in lines)
    print(f"RESULT: {ok} ok, {skipped} skipped (TODO pins), {failed} failed")
    return int(failed > 0)


if __name__ == "__main__":
    sys.exit(main())
