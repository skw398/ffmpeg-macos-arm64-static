#!/usr/bin/env python3
"""Collect dependency versions, licenses, FFmpeg build info and source patches."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from build_data import DATA, ROOT, archive_files, env_path, read_pins, read_licenses, source_files


def is_license(name):
    return re.search(r"(?:^|[-_. ])(?:LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|PATENTS)",
                     Path(name).name.upper()) is not None


def collect_licenses(pin, sources, downloads, destination, review):
    destination.mkdir(parents=True, exist_ok=True)
    required = set(review["licenseFiles"])
    found = set()
    count = 0

    def collect(relative, content, fallback_name):
        nonlocal count
        if not content:
            return
        if relative in required:
            if hashlib.sha256(content).hexdigest() != review["evidence"][relative]:
                raise ValueError(f"reviewed license text changed: {relative}")
            target = destination / relative
            found.add(relative)
        else:
            target = destination / fallback_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        count += 1

    if pin.commit:
        source = sources / pin.name
        paths = set(source_files(source, 2, is_license))
        paths |= {source / name for name in required if (source / name).is_file()}
        for path in sorted(paths):
            relative = path.relative_to(source).as_posix()
            collect(relative, path.read_bytes(), relative.replace("/", "_"))
    elif pin.archive(downloads).is_file():
        for name, content in archive_files(pin.archive(downloads),
                lambda name: is_license(name) or name.partition("/")[2] in required):
            relative = name.partition("/")[2]
            collect(relative, content, name.replace("/", "_"))
    if not count:
        raise ValueError("no non-empty license files found in pinned source")
    if found != required:
        raise ValueError("missing reviewed license files: " + ", ".join(sorted(required - found)))
    return count


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    downloads = env_path("DL", ROOT / "build/downloads")
    sources = env_path("SRC", ROOT / "build/src")
    prefix = env_path("PREFIX", ROOT / "build/prefix")
    output = env_path("OUT", ROOT / "build/artifacts/release")
    (output / "licenses").mkdir(parents=True, exist_ok=True)
    try:
        pins = read_pins()
        licenses = read_licenses(pins)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: cannot read license inventory: {error}", file=sys.stderr)
        return 1
    (output / "dependency-versions.txt").write_text(
        "".join(f"{pin.name}|{pin.version}\n" for pin in pins.values()))
    for pin in pins.values():
        try:
            collect_licenses(pin, sources, downloads, output / "licenses" / pin.name, licenses["packages"][pin.name])
        except (OSError, tarfile.TarError, ValueError) as error:
            print(f"ERROR: cannot collect licenses for {pin.name}: {error}", file=sys.stderr)
            return 1
    print(f"== dependency licenses included: {len(pins)}/{len(pins)} package(s)")
    for option, name in (("-version", "ffmpeg-version.txt"), ("-buildconf", "buildconf.txt")):
        with (output / name).open("w") as file:
            try:
                subprocess.run([str(prefix / "bin/ffmpeg"), option], stdout=file,
                               stderr=subprocess.STDOUT, check=False)
            except OSError as error:
                file.write(f"ERROR: {error}\n")
    if (ROOT / "patches").is_dir():
        shutil.copytree(ROOT / "patches", output / "patches", dirs_exist_ok=True)
        shutil.copyfile(DATA / "patches.txt", output / "patches/patches.txt")
        count = sum(path.is_file() for path in (output / "patches").rglob("*"))
        print(f"== source patches included: {count} file(s)")
    (output / "project").mkdir(exist_ok=True)
    for name in ("LICENSE", "LICENSING.md", "BUILDING.md", "SOURCE-CHANGES.md"):
        shutil.copyfile(ROOT / name, output / "project" / name)
    print(f"== release assets collected: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
