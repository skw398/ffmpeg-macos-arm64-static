#!/usr/bin/env python3
"""Check that pkg-config dependencies resolve inside the build prefix."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from build_data import ROOT, env_path


def requirements(text):
    modules = set()
    for line in text.splitlines():
        field, separator, value = line.partition(":")
        if not separator or field.strip() not in {"Requires", "Requires.private"}:
            continue
        modules.update(re.findall(
            r"([A-Za-z_][A-Za-z0-9_.+-]*)(?:\s*(?:[<>]=?|=)\s*[^\s,]+)?", value))
    return modules


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    prefix = env_path("PREFIX", ROOT / "build/prefix").resolve()
    pcdir = prefix / "lib/pkgconfig"
    if not pcdir.is_dir():
        print(f"ERROR: no pkg-config dir in the prefix ({pcdir})")
        return 1
    search = f"{pcdir}:{prefix / 'share/pkgconfig'}"
    env = os.environ | {"PKG_CONFIG_LIBDIR": search, "PKG_CONFIG_PATH": search}
    def pkg_config(*args):
        return subprocess.run(["pkg-config", *args], env=env, capture_output=True, text=True)

    print("== prefix .pc files embedding /opt/homebrew paths ==")
    embedding = [path for path in sorted(pcdir.rglob("*"))
                 if path.is_file() and "/opt/homebrew" in path.read_text(errors="replace")]
    for path in embedding:
        print(path)
    if not embedding:
        print("  (none)")
    bad = set()
    try:
        for pc in sorted(pcdir.glob("*.pc")):
            if pkg_config("--exists", pc.stem).returncode:
                bad.add(f"INVALID  {pc.stem}  (pkg-config cannot load {pc.name})")
            for module in sorted(requirements(pc.read_text())):
                directory = pkg_config("--variable=pcfiledir", module).stdout.strip()
                if not directory:
                    bad.add(f"MISSING  {module}  (required by {pc.name})")
                elif not Path(directory).resolve().is_relative_to(prefix):
                    bad.add(f"OUTSIDE  {module}  ->  {directory}  (required by {pc.name})")
    except OSError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if bad:
        print("== modules that do not resolve inside the prefix ==")
        print("\n".join(sorted(bad)))
        print("RESULT: FAILED (provide them in the prefix or disable the feature)")
        return 1
    print("RESULT: all required modules resolve inside the prefix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
