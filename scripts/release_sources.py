#!/usr/bin/env python3
"""Collect fixed release sources, or extract one dependency for a rebuild."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

from build_data import ROOT, env_path, read_pins

BUNDLE_NAME = "ffmpeg-macos-arm64-sources"
ROOT_FILES = ("LICENSE", "LICENSING.md", "BUILDING.md", "SOURCE-CHANGES.md", "deps.txt", "pyproject.toml",
              ".python-version", ".gitignore", "scripts/release_sources.py")


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def project_files(root):
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             check=True, capture_output=True).stdout.decode().split("\0")
    names = set(ROOT_FILES)
    for name in tracked:
        if (name.startswith(("scripts/", "patches/", ".github/actions/"))
                and not name.startswith("scripts/maintenance/")
                and "__pycache__" not in PurePosixPath(name).parts
                and not name.endswith(".pyc")):
            names.add(name)
    return sorted(names)


def safe_path(directory, name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"invalid source package path: {name}")
    result = Path(directory).joinpath(*path.parts)
    if not result.resolve().is_relative_to(Path(directory).resolve()):
        raise ValueError(f"source package path escapes its directory: {name}")
    return result


def read_manifest(package):
    manifest = json.loads((Path(package) / "manifest.json").read_text())
    if manifest["format"] != 1:
        raise ValueError("unexpected source package format")
    return manifest


def collect(root, pins, sources, downloads, output, build_info):
    """Archive immutable originals; the included scripts reproduce local edits."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        stage = Path(directory) / BUNDLE_NAME
        (stage / "upstream").mkdir(parents=True)
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        manifest = {"format": 1, "projectCommit": commit, "packages": {}, "files": {}}
        for name in project_files(root):
            source = root / name
            target = stage / "project" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for pin in pins.values():
            relative = f"upstream/{pin.name}.tar" if pin.commit else f"upstream/{pin.name}.tarball"
            target = stage / relative
            record = {"version": pin.version, "url": pin.url, "sourcePin": pin.checksum, "path": relative}
            source = sources / pin.name
            if pin.commit:
                head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
                if head != pin.commit:
                    raise ValueError(f"source commit mismatch: {pin.name}")
                with target.open("wb") as file:
                    subprocess.run(["git", "-C", str(source), "archive", "--format=tar",
                                    f"--prefix={pin.name}/", pin.commit], stdout=file,
                                   stderr=subprocess.PIPE, check=True)
            else:
                archive = pin.archive(downloads)
                if digest(archive) != pin.checksum.lower():
                    raise ValueError(f"source checksum mismatch: {pin.name}")
                shutil.copyfile(archive, target)
            # Cargo.lock is already fixed by the --locked build recipe. Vendor
            # every registry/git dependency, including its license notices.
            if (source / "Cargo.toml").is_file():
                lock = source / "Cargo.lock"
                if not lock.is_file():
                    raise ValueError(f"missing Cargo.lock: {pin.name}")
                with tarfile.open(target, "r:*") as archive:
                    members = [member for member in archive if member.name.count("/") == 1
                               and member.name.endswith("/Cargo.lock") and member.isfile()]
                    if len(members) != 1 or archive.extractfile(members[0]).read() != lock.read_bytes():
                        raise ValueError(f"Cargo.lock changed from the pinned source: {pin.name}")
                cargo = stage / "cargo" / pin.name
                cargo.mkdir(parents=True)
                vendor = cargo / "vendor"
                result = subprocess.run(["cargo", "vendor", "--locked", "--versioned-dirs", str(vendor)],
                                        cwd=source, check=True, capture_output=True, text=True)
                (cargo / "config.toml").write_text(result.stdout.replace(str(vendor), ".cargo-vendor"))
                shutil.copyfile(lock, cargo / "Cargo.lock")
                record["cargo"] = {"vendor": f"cargo/{pin.name}/vendor", "config": f"cargo/{pin.name}/config.toml",
                                   "lock": f"cargo/{pin.name}/Cargo.lock"}
            manifest["packages"][pin.name] = record
        (stage / "build-info").mkdir()
        for source in build_info:
            shutil.copyfile(source, stage / "build-info" / source.name)
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                if not file.resolve().is_relative_to(stage.resolve()):
                    raise ValueError(f"source file escapes staging directory: {file}")
                manifest["files"][file.relative_to(stage).as_posix()] = digest(file)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        temporary = output.with_suffix(output.suffix + ".tmp")
        try:
            with tarfile.open(temporary, "w:xz", preset=3, dereference=True) as archive:
                archive.add(stage, arcname=BUNDLE_NAME)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"== release sources collected: {output} ({len(pins)} pinned source packages)")


def extract(package, pin, destination):
    manifest = read_manifest(package)
    record = manifest["packages"][pin.name]
    if (record["version"], record["url"], record["sourcePin"]) != (pin.version, pin.url, pin.checksum):
        raise ValueError(f"source package pin mismatch: {pin.name}")
    source = safe_path(package, record["path"])
    actual = digest(source)
    if actual != manifest["files"][record["path"]] or (not pin.commit and actual != pin.checksum.lower()):
        raise ValueError(f"source package checksum mismatch: {pin.name}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        temp = Path(directory)
        with tarfile.open(source, "r:*") as archive:
            archive.extractall(temp, filter="data")
        roots = list(temp.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError(f"source archive must have one root directory: {pin.name}")
        tree = roots[0]
        if "cargo" in record:
            cargo = record["cargo"]
            for name, expected in manifest["files"].items():
                if name.startswith(f"cargo/{pin.name}/") and digest(safe_path(package, name)) != expected:
                    raise ValueError(f"Cargo source checksum mismatch: {name}")
            lock = safe_path(package, cargo["lock"])
            if digest(tree / "Cargo.lock") != digest(lock):
                raise ValueError(f"Cargo.lock mismatch: {pin.name}")
            config = tree / ".cargo/config.toml"
            config.parent.mkdir(exist_ok=True)
            original = config.read_text() if config.is_file() else ""
            legacy = tree / ".cargo/config"
            if legacy.is_file():
                raise ValueError(f"legacy Cargo configuration needs explicit handling: {pin.name}")
            if "source" in tomllib.loads(original):
                raise ValueError(f"upstream Cargo source configuration needs review: {pin.name}")
            shutil.copytree(safe_path(package, cargo["vendor"]), tree / ".cargo-vendor")
            config.write_text(original + "\n" + safe_path(package, cargo["config"]).read_text())
        if destination.exists():
            shutil.rmtree(destination)
        tree.rename(destination)
    print(f"== source package: {pin.name} -> {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("collect", help="CI only: assemble sources after the release build")
    bundle.add_argument("--out", type=Path, default=ROOT / "build/artifacts/sources" / (BUNDLE_NAME + ".tar.xz"))
    single = commands.add_parser("extract", help="prepare one pinned source from an unpacked source package")
    single.add_argument("--package", type=Path, required=True)
    single.add_argument("--name", required=True)
    single.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        pins = read_pins()
        if args.command == "collect":
            collect(ROOT, pins, env_path("SRC", ROOT / "build/src"), env_path("DL", ROOT / "build/downloads"), args.out,
                    [ROOT / "build/toolchain-versions.txt", ROOT / "build/artifacts/release/buildconf.txt"])
        else:
            extract(args.package, pins[args.name], args.destination)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        print(f"ERROR: release sources: {error}" + (f"\n{detail[-4000:]}" if detail else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
