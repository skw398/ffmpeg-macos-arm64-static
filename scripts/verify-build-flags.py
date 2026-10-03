#!/usr/bin/env python3
"""Check recipe flag names against the pinned source archives without building."""

import argparse
import re
import sys
import tarfile

from build_data import ROOT, archive_files, env_path, is_meson_options, read_pins, read_recipes


def flag_option(flag):
    for prefix in ("--enable-", "--disable-", "--with-", "--without-", "-D"):
        if flag.startswith(prefix):
            return flag.removeprefix(prefix).split("=", 1)[0]
    return ""


def invalid_options(archive, kind, options):
    declared = set()
    if kind == "meson":
        for _, content in archive_files(archive, is_meson_options):
            declared.update(re.findall(r"\boption\s*\(\s*['\"]([A-Za-z0-9_-]+)['\"]",
                                       content.decode("utf-8", "replace")))
    if declared:
        return options - declared, "not a declared meson option"
    missing = set(options)
    for _, content in archive_files(archive):
        missing = {option for option in missing if option.encode() not in content}
        if not missing:
            break
    return missing, "not found in source"


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    downloads = env_path("DL", ROOT / "build/downloads")
    pins = read_pins()
    failed = False
    for recipe in read_recipes():
        if recipe.kind not in {"cmake", "meson", "autotools"}:
            continue
        pin = pins.get(recipe.name)
        if not pin or pin.commit or not pin.checksum:
            continue
        archive = pin.archive(downloads)
        if not archive.is_file():
            print(f"skip {recipe.name} (missing tarball)")
            continue
        flags = {flag: flag_option(flag) for flag in recipe.flags.split()}
        flags = {flag: option for flag, option in flags.items()
                 if option and not option.endswith("_DIR") and not option.startswith("CMAKE_")}
        if not flags:
            continue
        try:
            missing, reason = invalid_options(archive, recipe.kind, set(flags.values()))
            for flag, option in flags.items():
                if option in missing:
                    print(f"INVALID {recipe.name}: {flag} ('{option}' {reason})")
                    failed = True
        except (OSError, tarfile.TarError) as error:
            print(f"ERROR: cannot check {recipe.name}: {error}")
            failed = True
    print("RESULT: build flag lint " + ("FAILED" if failed else "passed"))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
