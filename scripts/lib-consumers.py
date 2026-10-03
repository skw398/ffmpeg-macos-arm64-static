#!/usr/bin/env python3
"""Map FFmpeg external libraries to consumers, or check enabled consumers.

Map mode accepts a configure file; otherwise read the pinned FFmpeg archive.
Check mode uses disabled components, generated headers, or the built binary.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from build_data import DATA, ROOT, archive_text, read_pins, rows


LIB_LIST = re.compile(r'^EXTERNAL_LIBRARY(?:_GPL|_NONFREE|_VERSION3|_GPLV3)?_LIST="')
DEPENDENCY = re.compile(r"^([A-Za-z0-9_]+)_(deps_any|deps|select|suggest)=(.*)$")
COMPONENT_LISTS = {
    "encoder": "encoders", "decoder": "decoders", "filter": "filters",
    "demuxer": "demuxers", "muxer": "muxers", "parser": "parsers",
    "bsf": "bsfs", "protocol": "protocols", "indev": "devices",
    "outdev": "devices", "hwaccel": "hwaccels",
}


def configure_text(path=None):
    if path:
        return Path(path).read_text(errors="replace")
    pin = read_pins()["ffmpeg"]
    return archive_text(pin.archive(ROOT / "build/downloads"), f"ffmpeg-{pin.version}/configure")


def map_consumers(text):
    libraries = {}
    in_list = False
    dependencies = []
    for line in text.splitlines():
        if LIB_LIST.match(line):
            in_list = True
        elif in_list and line.strip() == '"':
            in_list = False
        elif in_list:
            for library in line.split():
                if not library.startswith("$"):
                    libraries.setdefault(library, {"hard": [], "suggest": []})
        elif match := DEPENDENCY.match(line):
            dependencies.append(match.groups())
    for component, kind, value in dependencies:
        for library in value.replace('"', "").split():
            if library in libraries:
                libraries[library]["suggest" if kind == "suggest" else "hard"].append(component)
    return libraries


def list_names(text):
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if (not line or re.fullmatch(r"[A-Za-z][A-Za-z ]*:", line)
                or re.match(r"[A-Za-z.|]+ = ", line) or re.fullmatch(r"-+", line)):
            continue
        fields = line.split()
        names.add(fields[1] if len(fields) > 1 and re.fullmatch(r"[A-Z.]+", fields[0]) else fields[0])
    return names


class ConsumerCheck:
    def __init__(self, disabled=(), headers=(), binary=None):
        self.disabled = set(disabled)
        self.binary = binary
        self.lists = {}
        self.macros = set()
        self.with_headers = bool(headers)
        for path in headers:
            files = [path / "config.h", path / "config_components.h"] if path.is_dir() else [path]
            for file in files:
                self.macros.update(re.findall(
                    r"^#define[ \t]+(CONFIG_[A-Z0-9_]+)[ \t]+1(?:[ \t]|$)",
                    file.read_text(), re.MULTILINE))

    def names(self, kind):
        if kind not in self.lists:
            result = subprocess.run([str(self.binary), "-hide_banner", f"-{kind}"],
                                    capture_output=True, text=True, check=True)
            self.lists[kind] = list_names(result.stdout)
        return self.lists[kind]

    def enabled(self, component):
        if self.binary:
            name, separator, suffix = component.rpartition("_")
            return bool(separator and suffix in COMPONENT_LISTS and name in self.names(COMPONENT_LISTS[suffix]))
        if self.with_headers:
            return f"CONFIG_{component.upper()}" in self.macros
        return component not in self.disabled

    @property
    def where(self):
        if self.binary:
            return "listed by the binary"
        return "enabled in config.h" if self.with_headers else "enabled (pre-configure)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--disabled", default="", help="space-separated disabled components")
    parser.add_argument("--config-header", type=Path, action="append", default=[])
    parser.add_argument("--binary", type=Path)
    parser.add_argument("items", nargs="*", help="configure file in map mode; libraries in check mode")
    args = parser.parse_args()
    if args.check and not args.items:
        parser.error("--check requires at least one library")
    if not args.check and len(args.items) > 1:
        parser.error("map mode accepts one configure file")
    if args.binary and not os.access(args.binary, os.X_OK):
        parser.error(f"--binary is not executable: {args.binary}")
    try:
        mapping = map_consumers(configure_text(args.items[0] if args.items and not args.check else None))
        if not args.check:
            for library, consumers in mapping.items():
                print(f"{library}|{' '.join(consumers['hard'])}|{' '.join(consumers['suggest'])}")
            return 0
        check = ConsumerCheck(args.disabled.split(), args.config_header, args.binary)
        overrides = {}
        if args.binary:
            path = Path(os.environ.get("OVERRIDES") or DATA / "artifact-consumers.txt")
            if path.is_file():
                overrides = {row[0]: row[1:] for row in rows(path)}
        failed = False
        skipped = 0
        for library in args.items:
            if library in overrides:
                kind, listing, name = overrides[library]
                if kind == "skip":
                    print(f"skip         {library} ({name})")
                    skipped += 1
                elif name in check.names(listing):
                    print(f"ok           {library} -> {listing}:{name}")
                else:
                    print(f"NO ARTIFACT  {library} (missing {listing}:{name})")
                    failed = True
                continue
            consumers = mapping.get(library, {"hard": [], "suggest": []})
            hard = [component for component in consumers["hard"] if check.enabled(component)]
            suggest = [component for component in consumers["suggest"] if check.enabled(component)]
            if hard:
                print(f"ok           {library} -> {' '.join(hard)}")
            elif suggest:
                print(f"ok (suggest) {library} -> {' '.join(suggest)}")
            else:
                print(f"NO CONSUMER  {library}")
                failed = True
        if not failed:
            print(f"RESULT: all checked libraries have a consumer {check.where}")
            if skipped:
                print(f"        ({skipped} library/libraries not confirmable from ffmpeg lists)")
        return int(failed)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
