#!/usr/bin/env python3
"""Collect dependency versions, licenses, FFmpeg build info and source patches."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from build_data import DATA, ROOT, archive_files, env_path, read_pins, source_files


def is_license(name):
    return re.search(r"(?:^|[-_. ])(?:LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|PATENTS)",
                     Path(name).name.upper()) is not None


def collect_licenses(pin, sources, downloads, destination):
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    if pin.commit:
        source = sources / pin.name
        for path in source_files(source, 2, is_license):
            if path.stat().st_size == 0:
                continue
            name = str(path.relative_to(source)).replace("/", "_")
            shutil.copyfile(path, destination / name)
            count += 1
    elif pin.archive(downloads).is_file():
        for name, content in archive_files(pin.archive(downloads), is_license):
            if not content:
                continue
            (destination / name.replace("/", "_")).write_bytes(content)
            count += 1
    if not count:
        raise ValueError("no non-empty license files found in pinned source")
    return count


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    downloads = env_path("DL", ROOT / "build/downloads")
    sources = env_path("SRC", ROOT / "build/src")
    prefix = env_path("PREFIX", ROOT / "build/prefix")
    output = env_path("OUT", ROOT / "build/artifacts/release")
    (output / "licenses").mkdir(parents=True, exist_ok=True)
    pins = read_pins()
    (output / "dependency-versions.txt").write_text(
        "".join(f"{pin.name}|{pin.version}\n" for pin in pins.values()))
    for pin in pins.values():
        try:
            collect_licenses(pin, sources, downloads, output / "licenses" / pin.name)
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
    print(f"== release assets collected: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
