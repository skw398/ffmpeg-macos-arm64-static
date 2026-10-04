#!/usr/bin/env python3
"""Report rejected, unused or redundant build flags and transform activity."""

import argparse
from collections import Counter
from pathlib import Path
import re
import sys
import tarfile

# Maintenance reads the normal build data; normal build scripts are independent.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_data import ROOT, archive_files, env_path, is_meson_options, read_pins, read_recipes, source_files


GLOBAL_CMAKE = {
    "CMAKE_INSTALL_PREFIX", "CMAKE_BUILD_TYPE", "CMAKE_PREFIX_PATH",
    "CMAKE_POLICY_VERSION_MINIMUM", "CMAKE_OSX_DEPLOYMENT_TARGET",
    "BUILD_SHARED_LIBS", "BUILD_TESTING",
}


def norm_bool(value):
    return {"enabled": "true", "disabled": "false"}.get(value, value)


def meson_defaults(text):
    defaults = {}
    for option in re.split(r"\boption\s*\(", text)[1:]:
        name = re.match(r"\s*['\"]([^'\"]+)['\"]", option)
        value = re.search(r"\bvalue\s*:\s*['\"]?([A-Za-z0-9_.-]+)", option)
        if name and value:
            defaults[name[1]] = norm_bool(value[1])
    return defaults


def options_text(name, pin, sources, downloads):
    for path in source_files(sources / name, 2, is_meson_options):
        return path.read_text(errors="replace")
    if pin and not pin.commit and pin.archive(downloads).is_file():
        for _, content in archive_files(pin.archive(downloads), is_meson_options):
            return content.decode("utf-8", "replace")
    return ""


def unused_cmake(text):
    unused = set()
    in_warning = False
    for line in text.splitlines():
        if "CMake Warning (unused-cli)" in line:
            in_warning = True
        elif in_warning:
            if re.fullmatch(r"\s+[A-Za-z_][A-Za-z0-9_]*", line):
                unused.add(line.strip())
            elif line and not line[0].isspace():
                in_warning = False
    return unused


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    logs = env_path("LOGS", ROOT / "build/logs")
    sources = env_path("SRC", ROOT / "build/src")
    downloads = env_path("DL", ROOT / "build/downloads")
    if not logs.is_dir():
        print(f"ERROR: no build logs at {logs} (run build-deps.sh first)", file=sys.stderr)
        return 1
    recipes = read_recipes()
    pins = read_pins()
    log_texts = {path.stem: path.read_text(errors="replace") for path in sorted(logs.glob("*.log"))}
    built = sum(recipe.name in log_texts for recipe in recipes)
    print(f"== build coverage: {built} of {len(recipes)} libraries were rebuilt in this run ==")
    if built < len(recipes):
        print("  NOTE: the counts below only cover those libraries; read them on a full")
        print("        build (clean_build) before concluding anything.")

    print("\n== autotools flags rejected by configure (unrecognized options) ==")
    found = False
    for name, text in log_texts.items():
        options = {option.strip() for line in text.splitlines() if "unrecognized options:" in line
                   for option in line.split("unrecognized options:", 1)[1].split(",") if option.strip()}
        if options:
            print(f"{name:<16} {' '.join(sorted(options))}")
            found = True
    if not found:
        print("  (none)")

    print("\n== CMake -D variables reported as unused (unused-cli) ==")
    found = False
    global_counts = Counter()
    for name, text in log_texts.items():
        for variable in sorted(unused_cmake(text)):
            if variable in GLOBAL_CMAKE:
                global_counts[variable] += 1
            else:
                print(f"{name:<16} {variable:<30} [per-lib] trim candidate")
                found = True
    if not found:
        print("  (no per-library candidates)")
    if global_counts:
        print("  [global] also unused somewhere (expected): " + "; ".join(
            f"{variable} in {global_counts[variable]} library/libraries" for variable in sorted(global_counts)))

    print("\n== meson -D flags equal to the declared default (redundant) ==")
    found = False
    for recipe in recipes:
        if recipe.kind not in {"meson", "custom"}:
            continue
        try:
            defaults = meson_defaults(options_text(recipe.name, pins.get(recipe.name), sources, downloads))
        except (OSError, tarfile.TarError) as error:
            print(f"  WARNING: cannot read options for {recipe.name}: {error}")
            continue
        for flag in recipe.flags.split():
            if flag.startswith("-D") and "=" in flag:
                option, value = flag[2:].split("=", 1)
                if option in defaults and norm_bool(value) == defaults[option]:
                    print(f"{recipe.name:<16} {flag:<34} (default {defaults[option]}) trim candidate")
                    found = True
    if not found:
        print("  (none)")

    print("\n== patch/transform activity (a transform with 0 occurrences is droppable) ==")
    found = False
    for name, text in log_texts.items():
        patches = sorted({line for line in text.splitlines() if line.startswith("== patch")})
        if patches:
            print(f"{name:<16} {'; '.join(patches)}")
            found = True
    if not found:
        print("  (none)")
    print("\nRESULT: report only (nothing here fails the build)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
