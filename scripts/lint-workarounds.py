#!/usr/bin/env python3
"""Report source, environment and data signals that workarounds may be removable."""

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

from build_data import (
    DATA, HERE, ROOT, Recipe, archive_files, archive_text, env_path, is_cmake,
    read_pins, read_recipes, rows, source_files,
)


WERROR = re.compile(r" ?-Werror([^=A-Za-z0-9_,-]|$)")
POLICY_FLAGS = set("""
--enable-static --disable-shared --enable-pic --default-library=static no-shared
-DBUILD_SHARED_LIBS=OFF -DPNG_SHARED=OFF -DSDL_SHARED=OFF -DSDL_STATIC=ON
-DOAPV_BUILD_SHARED_LIB=OFF -DENABLE_SHARED=OFF -DBUILD_TESTING=OFF
--release --library-type staticlib --locked
""".split())


def werror_sources(recipes, pins, sources, downloads):
    print("== [B] CMake sources adding -Werror (the CMake -Werror strip) ==")
    hits = []
    scanned = 0
    for recipe in recipes:
        pin = pins.get(recipe.name)
        if not pin:
            continue
        source = sources / recipe.name
        # Working trees are patched by lib.sh; scan committed/archive sources.
        if pin.commit:
            if not (source / ".git").is_dir():
                continue
            result = subprocess.run(["git", "-C", str(source), "grep", "-lE", WERROR.pattern,
                                     "HEAD", "--", "*CMakeLists.txt", "*.cmake"],
                                    capture_output=True, text=True)
            if result.returncode not in (0, 1):
                raise RuntimeError(f"cannot scan committed sources for {recipe.name}: {result.stderr.strip()}")
            hits.extend(f"{recipe.name}/{path.removeprefix('HEAD:')}" for path in result.stdout.splitlines())
        else:
            archive = pin.archive(downloads)
            if not archive.is_file():
                continue
            hits.extend(f"{recipe.name}/{name}" for name, content in archive_files(
                archive, lambda name: is_cmake(Path(name).name))
                if WERROR.search(content.decode("utf-8", "replace")))
        scanned += 1
    if not scanned:
        print("  (no pinned sources present: skipped)")
    elif hits:
        print("  still adding a bare -Werror: " + " ".join(hits))
        print("  -> the CMake -Werror strip is still needed")
    else:
        print(f"  none of the {scanned} project(s) adds a bare -Werror")
        print("  -> the CMake -Werror strip can be dropped")
        return True
    return False


def sdk_pkgconfig():
    print("== [C] SDK pkg-config files (libxml-2.0.pc / zlib.pc shims) ==")
    sdk = Path(subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True,
                              text=True, check=True).stdout.strip())
    provided = [name for name in ("libxml-2.0.pc", "zlib.pc")
                if any((sdk / directory / name).is_file()
                       for directory in ("usr/lib/pkgconfig", "usr/share/pkgconfig"))]
    if provided:
        print("  now provided by the SDK: " + " ".join(provided) + " -> drop the matching shim in build-deps.sh")
    else:
        print("  (still absent: shims still needed)")
    return bool(provided)


def ffmpeg_stdcpp(pins, downloads):
    print("\n== [C] FFmpeg -lstdc++ requirement (libstdc++.tbd alias) ==")
    pin = pins["ffmpeg"]
    archive = pin.archive(downloads)
    if not archive.is_file():
        print(f"  (no pinned ffmpeg tarball at {archive}: skipped)")
    elif "-lstdc++" in archive_text(archive, f"ffmpeg-{pin.version}/configure"):
        print("  ffmpeg configure still asks for -lstdc++: alias still needed")
    else:
        print("  ffmpeg configure no longer asks for -lstdc++ -> drop the libstdc++.tbd alias")
        return True
    return False


def cmake_minimum(recipes, sources):
    print("\n== [D] CMake projects declaring < 3.5 (CMAKE_POLICY_VERSION_MINIMUM) ==")
    low = []
    scanned = 0
    for recipe in recipes:
        source = sources / recipe.name
        if not source.is_dir():
            continue
        scanned += 1
        versions = {tuple(map(int, version))
                    for path in source_files(source, 3, lambda name: name == "CMakeLists.txt")
                    for version in re.findall(r"cmake_minimum_required\(\s*VERSION\s+(\d+)\.(\d+)",
                                              path.read_text(errors="replace"))}
        if versions and min(versions) < (3, 5):
            major, minor = min(versions)
            low.append(f"{recipe.name}({major}.{minor})")
    if not scanned:
        print("  (no CMake sources present: skipped)")
    elif low:
        print("  still declaring < 3.5: " + " ".join(low))
        print("  -> CMAKE_POLICY_VERSION_MINIMUM=3.5 still needed")
    else:
        print(f"  none of the {scanned} CMake projects declares < 3.5")
        print("  -> CMAKE_POLICY_VERSION_MINIMUM=3.5 can be dropped")
        return True
    return False


def cmake_testing(recipes, sources):
    print("\n== [D] CMake projects that never read BUILD_TESTING (-DBUILD_TESTING=OFF) ==")
    reads = []
    never = []
    for recipe in recipes:
        source = sources / recipe.name
        if source.is_dir():
            used = any(re.search(r"BUILD_TESTING|include\(\s*CTest", path.read_text(errors="replace"))
                       for path in source_files(source, 3, is_cmake))
            (reads if used else never).append(recipe.name)
    if not reads and not never:
        print("  (no CMake sources present: skipped)")
    elif not reads:
        print("  no CMake project reads BUILD_TESTING: " + " ".join(never))
        print("  -> -DBUILD_TESTING=OFF can be dropped")
        return True
    else:
        print("  reads BUILD_TESTING: " + " ".join(reads))
        if never:
            print("  never reads it (the flag is inert there): " + " ".join(never))
        print("  -> -DBUILD_TESTING=OFF still needed")
    return False


def artifact_skips(prefix):
    print("\n== [I] artifact-consumers skips (would the plain mapping work now?) ==")
    binary = prefix / "bin/ffmpeg"
    if not os.access(binary, os.X_OK):
        print(f"  (no ffmpeg binary at {binary}: skipped)")
        return False
    overrides = list(rows(DATA / "artifact-consumers.txt"))
    skips = [row[0] for row in overrides if row[1] == "skip"]
    fired = False
    if not skips:
        print("  (no skip entries)")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overrides.txt"
        for library in skips:
            path.write_text("".join("|".join(row) + "\n" for row in overrides if row[0] != library))
            result = subprocess.run([sys.executable, str(HERE / "lib-consumers.py"), "--check",
                                     "--binary", str(binary), library],
                                    env=os.environ | {"OVERRIDES": str(path)}, capture_output=True)
            if result.returncode == 0:
                print(f"  {library}: the plain mapping now works -> remove the skip entry")
                fired = True
            elif result.returncode == 1:
                print(f"  {library}: still needs the skip")
            else:
                print(f"  {library}: unable to check: {result.stderr.decode().strip()}")
    return fired


def meson_python():
    print("\n== [J] python3 externally managed (meson venv) ==")
    # Inspect Meson's Python, not the uv-managed interpreter running this lint.
    python = env_path("TOOLS", Path.home() / "tools") / "meson-venv/bin/python3"
    result = subprocess.run([str(python), "-c",
                             "import pathlib, sysconfig; "
                             "print(int((pathlib.Path(sysconfig.get_path('stdlib')) / 'EXTERNALLY-MANAGED').exists()))"],
                            capture_output=True, text=True, check=True)
    if result.stdout.strip() == "1":
        print("  python3 is still externally managed (PEP 668): the meson venv is still needed")
        return False
    print("  python3 is no longer externally managed -> the meson venv may be droppable")
    return True


def private_deps(prefix):
    print("== [G] private deps FFmpeg's --extra-libs supplies ==")
    pcdir = prefix / "lib/pkgconfig"
    if not pcdir.is_dir():
        print("  (no pkg-config dir in the prefix: skipped)")
        return False
    search = f"{pcdir}:{prefix / 'share/pkgconfig'}"
    env = os.environ | {"PKG_CONFIG_LIBDIR": search, "PKG_CONFIG_PATH": search}
    missing = []
    for module, tokens in (("libjxl_threads", ["-lc++"]), ("libssh", ["-lssl", "-lcrypto", "-lz"]),
                           ("chromaprint", ["Accelerate"])):
        result = subprocess.run(["pkg-config", "--static", "--libs", module],
                                env=env, capture_output=True, text=True)
        missing.extend(f"{module}:{token}" for token in tokens
                       if result.returncode or token not in result.stdout.split())
    if missing:
        print("  still not declared by the .pc file: " + " ".join(missing))
        print("  -> FFmpeg's --extra-libs is still needed")
    else:
        print("  every one of them is declared: --extra-libs can be dropped")
    return not missing


def custom_buildsystems(recipes, sources):
    print("\n== [H] custom builders: did upstream gain a standard build system? ==")
    hits = []
    for recipe in recipes:
        if recipe.kind not in {"custom", "make-copy"}:
            continue
        files = ["CMakeLists.txt", "meson.build"]
        if recipe.kind == "make-copy":
            files.append("configure")
        hits.extend(f"{recipe.name}({file})" for file in files
                    if (sources / recipe.name / file).is_file())
    if hits:
        print("  standard build system found: " + " ".join(hits) + " -> consider switching to it")
    else:
        print("  (none: custom builders still needed)")
    return bool(hits)


def quirc_stderr(sources):
    print("\n== [H] quirc's makefile capturing pkg-config stderr (SDL_CFLAGS) ==")
    makefile = sources / "quirc/Makefile"
    if not makefile.is_file():
        print("  (no quirc source present: skipped)")
        return False
    hits = [f"{number}:{line}" for number, line in enumerate(makefile.read_text().splitlines(), 1)
            if re.search(r"CFLAGS.*pkg-config.*2>&1", line)]
    if hits:
        print("  still capturing stderr:")
        for hit in hits[:3]:
            print(f"    {hit}")
        print("  -> the SDL_CFLAGS= workaround is still needed")
    else:
        print("  no CFLAGS assignment captures pkg-config stderr")
        print("  -> the SDL_CFLAGS= workaround can be dropped")
    return not hits


def trial_ids(recipes):
    print("\n== [T] trial workaround ids (trial-workarounds.txt vs the scripts) ==")
    trials = list(rows(DATA / "trial-workarounds.txt"))
    listed = {row[0] for row in trials}
    consulted = {trial for name in ("build-deps.sh", "build-ffmpeg.sh", "lib.sh")
                 for trial in re.findall(r"trial_(?:disabled|flag) ([a-z0-9-]+)", (HERE / name).read_text())}
    unused = sorted(listed - consulted)
    unlisted = sorted(consulted - listed)
    if unused:
        print("  listed but never consulted: " + " ".join(unused))
        print("  -> the trial would silently do nothing; wire it up or drop the id")
    if unlisted:
        print("  consulted but not listed: " + " ".join(unlisted) + " -> add it to trial-workarounds.txt")
    if not unused and not unlisted:
        print("  every listed id is consulted, and every consulted id is listed")
    names = {recipe.name for recipe in recipes} | {"*", "ffmpeg"}
    no_mapping = [row[0] for row in trials if not row[3]]
    bad_libraries = [f"{row[0]}:{name}" for row in trials for name in row[3].split() if name not in names]
    if no_mapping:
        print("  no affected-library mapping: " + " ".join(no_mapping) + " -> a fast trial would fail")
    if bad_libraries:
        print("  mapping names that are not recipes in build-deps.txt: " + " ".join(bad_libraries))
    if not no_mapping and not bad_libraries:
        print("  every id has an affected-library mapping, and the names are real recipes")
    return False


def flag_classification(recipes):
    print("\n== [E] per-library flags: classification (flag-categories.txt) ==")
    categories = list(rows(DATA / "flag-categories.txt"))
    classified = {(row[0], row[1]) for row in categories}
    flags = {(recipe.name, flag) for recipe in recipes for flag in recipe.flags.split() if flag not in POLICY_FLAGS}
    recipe_flags = {recipe.name: set(recipe.flags.split()) for recipe in recipes}
    missing = [f"{name}:{flag}" for name, flag in sorted(flags - classified)]
    stale, bad_purpose, bad_target = [], [], []
    counts = Counter()
    for library, flag, purpose, target, note in categories:
        if purpose not in {"build", "dep", "extras", "find", "feature"}:
            bad_purpose.append(f"{library}:{flag}({purpose})")
            continue
        counts[purpose] += 1
        if purpose == "dep" and not target:
            bad_target.append(f"{library}:{flag}")
        if flag not in recipe_flags.get(library, set()):
            stale.append(f"{library}:{flag}")
    if missing:
        print("  unclassified flags: " + " ".join(missing))
        print("  -> add them to flag-categories.txt (with a purpose)")
    if stale:
        print("  classified but not in build-deps.txt: " + " ".join(stale) + " -> remove them")
    if bad_purpose:
        print("  unknown purpose (build/dep/extras/find/feature only): " + " ".join(bad_purpose))
    if bad_target:
        print("  dep without a target (the dependency it avoids): " + " ".join(bad_target))
    if not any((missing, stale, bad_purpose, bad_target)):
        summary = " ".join(f"{purpose}={counts[purpose]}" for purpose in ("build", "dep", "extras", "find", "feature"))
        print(f"  all {len(flags)} flags classified ({summary})")
    print(f"  trial targets (build+dep): {counts['build'] + counts['dep']} -> run drop_flags one at a time (see the runbook)")
    return False


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    sources = env_path("SRC", ROOT / "build/src")
    downloads = env_path("DL", ROOT / "build/downloads")
    prefix = env_path("PREFIX", ROOT / "build/prefix")
    fired = False
    try:
        recipes = read_recipes()
        pins = read_pins()
        projects = recipes + [Recipe("chromaprint", "cmake", "")]
        cmake = [recipe for recipe in projects if recipe.kind == "cmake"]
        checks = [
            lambda: werror_sources(projects, pins, sources, downloads),
            sdk_pkgconfig,
            lambda: ffmpeg_stdcpp(pins, downloads),
            lambda: cmake_minimum(cmake, sources),
            lambda: cmake_testing(cmake, sources),
            lambda: artifact_skips(prefix),
            meson_python,
            lambda: private_deps(prefix),
            lambda: custom_buildsystems(recipes, sources),
            lambda: quirc_stderr(sources),
            lambda: trial_ids(recipes),
            lambda: flag_classification(recipes),
        ]
        for check in checks:
            try:
                fired = check() or fired
            except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as error:
                print(f"  WARNING: unable to complete this check: {error}")
    except (OSError, ValueError) as error:
        print(f"WARNING: cannot read workaround data: {error}")
    if fired:
        print("\nRESULT: some workarounds may be removable (report only; see docs/BUILD-WORKAROUNDS.md)")
    else:
        print("\nRESULT: no workaround looks removable yet (report only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
