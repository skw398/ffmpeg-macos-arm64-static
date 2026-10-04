#!/usr/bin/env python3
"""Verify and unpack a release's source package for the maintenance CI rebuild."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import read_pins
from release_sources import BUNDLE_NAME, digest

TOOLS = ("XCODE_VERSION", "RUST_VERSION", "CARGO_C_VERSION", "CMAKE_VERSION",
         "NINJA_VERSION", "MESON_VERSION", "UV_VERSION", "UV_SHA256", "PYTHON_VERSION")


def prepare(artifacts, output):
    archives = list(artifacts.glob("*-sources.tar.xz"))
    if len(archives) != 1:
        raise ValueError("expected exactly one release source archive")
    source = archives[0]
    checksums = []
    for line in (artifacts / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](?:\./)?([^/]+)", line)
        if match is None:
            raise ValueError("invalid SHA256SUMS line")
        if match[2] == source.name:
            checksums.append(match[1])
    if checksums != [digest(source)]:
        raise ValueError("missing, duplicate or mismatched source checksum")
    output.mkdir(parents=True)
    with tarfile.open(source, "r:xz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if (not parts or parts[0] != BUNDLE_NAME or ".." in parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError(f"unexpected source archive entry: {member.name}")
        archive.extractall(output, filter="data")
    package = output / BUNDLE_NAME
    manifest = json.loads((package / "manifest.json").read_text())
    hashes = {file.relative_to(package).as_posix(): digest(file)
              for file in package.rglob("*") if file.is_file() and file != package / "manifest.json"}
    if manifest["format"] != 1 or hashes != manifest["files"]:
        raise ValueError("source manifest file inventory or checksum mismatch")
    project = package / "project"
    pins = read_pins(project / "deps.txt")
    if set(pins) != set(manifest["packages"]):
        raise ValueError("source pin inventory mismatch")
    for pin in pins.values():
        record = manifest["packages"][pin.name]
        expected = f"upstream/{pin.name}.tar" if pin.commit else f"upstream/{pin.name}.tarball"
        if ((record["version"], record["url"], record["sourcePin"], record["path"])
                != (pin.version, pin.url, pin.checksum, expected) or expected not in hashes
                or (not pin.commit and hashes[expected] != pin.checksum.lower())):
            raise ValueError(f"source pin mismatch: {pin.name}")
    action = (project / ".github/actions/build/action.yml").read_text()
    versions = {}
    for name in TOOLS:
        values = re.findall(r'^\s+' + name + r': "([a-zA-Z0-9._-]+)"$', action, re.M)
        if len(values) != 1:
            raise ValueError(f"expected one pinned tool version in the bundled Action: {name}")
        versions[name] = values[0]
    if versions["PYTHON_VERSION"] != (project / ".python-version").read_text().strip():
        raise ValueError("bundled Python version mismatch")
    print(f"== source rebuild prepared: {manifest['projectCommit']} ({len(pins)} pinned sources)")
    return package.resolve(), versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        package, versions = prepare(args.artifacts, args.out)
        with Path(os.environ["GITHUB_ENV"]).open("a") as file:
            file.write(f"SOURCE_PACKAGE={package}\n")
            for name, value in versions.items():
                file.write(f"{name}={value}\n")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as file:
            file.write(f"project={package / 'project'}\n")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(f"ERROR: source rebuild preparation: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
