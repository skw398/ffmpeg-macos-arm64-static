"""Source-package collection and preparation use data fixtures, never builds."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import Pin
import release_sources as sources


def archive_bytes(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return data.getvalue()


class ReleaseSourcesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "package"
        self.package.mkdir()
        self.pin = Pin("fixture", "tag", "https://example.invalid/fixture", "COMMIT=" + "b" * 40, "MIT")
        self.archive = archive_bytes({"fixture/LICENSE": b"fixture license", "fixture/source.c": b"source"})
        (self.package / "upstream").mkdir()
        (self.package / "upstream/fixture.tar").write_bytes(self.archive)
        self.manifest = {"format": 1, "projectCommit": "a" * 40,
                         "packages": {"fixture": {"version": self.pin.version, "url": self.pin.url,
                                                   "sourcePin": self.pin.checksum, "path": "upstream/fixture.tar"}},
                         "files": {"upstream/fixture.tar": hashlib.sha256(self.archive).hexdigest()}}
        self.destination = self.root / "build/src/fixture"
        self.save_manifest()

    def save_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest))

    def test_extract_fixed_git_and_tar_sources_without_fetching(self):
        with contextlib.redirect_stdout(io.StringIO()):
            sources.extract(self.package, self.pin, self.destination)
            self.assertEqual((self.destination / "source.c").read_bytes(), b"source")
            self.pin = Pin("fixture", "tag", self.pin.url, hashlib.sha256(self.archive).hexdigest(), "MIT")
            self.manifest["packages"]["fixture"]["sourcePin"] = self.pin.checksum
            self.save_manifest()
            sources.extract(self.package, self.pin, self.destination)
            self.assertEqual((self.destination / "LICENSE").read_bytes(), b"fixture license")

    def test_bad_pin_or_archive_preserves_existing_destination(self):
        self.destination.mkdir(parents=True)
        (self.destination / "keep").write_text("existing")
        self.manifest["packages"]["fixture"]["sourcePin"] = "COMMIT=" + "c" * 40
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            sources.extract(self.package, self.pin, self.destination)
        self.manifest["packages"]["fixture"]["sourcePin"] = self.pin.checksum
        self.save_manifest()
        (self.package / "upstream/fixture.tar").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            sources.extract(self.package, self.pin, self.destination)
        self.assertEqual((self.destination / "keep").read_text(), "existing")

    def test_archive_traversal_and_multiple_roots_are_rejected(self):
        for files in ({"fixture/../../escape": b"bad"}, {"one/file": b"one", "two/file": b"two"}):
            with self.subTest(files=files):
                data = archive_bytes(files)
                (self.package / "upstream/fixture.tar").write_bytes(data)
                self.manifest["files"]["upstream/fixture.tar"] = hashlib.sha256(data).hexdigest()
                self.save_manifest()
                with self.assertRaises((ValueError, tarfile.TarError)):
                    sources.extract(self.package, self.pin, self.destination)
        self.assertFalse((self.root / "escape").exists())

    def add_cargo(self):
        files = {"fixture/Cargo.toml": b"[package]\nname='fixture'\n", "fixture/Cargo.lock": b"version = 4\n",
                 "fixture/.cargo/config.toml": b"[build]\njobs = 1\n"}
        data = archive_bytes(files)
        (self.package / "upstream/fixture.tar").write_bytes(data)
        self.manifest["files"]["upstream/fixture.tar"] = hashlib.sha256(data).hexdigest()
        cargo_files = {"cargo/fixture/config.toml": b'[source.crates-io]\nreplace-with="vendored-sources"\n[source.vendored-sources]\ndirectory=".cargo-vendor"\n',
                       "cargo/fixture/Cargo.lock": b"version = 4\n",
                       "cargo/fixture/vendor/crate-1.0/LICENSE": b"crate license",
                       "cargo/fixture/vendor/crate-1.0/.cargo-checksum.json": b'{}'}
        for name, content in cargo_files.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.manifest["files"][name] = hashlib.sha256(content).hexdigest()
        self.manifest["packages"]["fixture"]["cargo"] = {"vendor": "cargo/fixture/vendor", "config": "cargo/fixture/config.toml",
                                                          "lock": "cargo/fixture/Cargo.lock"}
        self.save_manifest()

    def test_cargo_vendor_configuration_is_relocatable_and_preserves_settings(self):
        self.add_cargo()
        with contextlib.redirect_stdout(io.StringIO()):
            sources.extract(self.package, self.pin, self.destination)
        configuration = sources.tomllib.loads((self.destination / ".cargo/config.toml").read_text())
        self.assertEqual(configuration["build"]["jobs"], 1)
        self.assertEqual(configuration["source"]["vendored-sources"]["directory"], ".cargo-vendor")
        self.assertEqual((self.destination / ".cargo-vendor/crate-1.0/LICENSE").read_bytes(), b"crate license")
        (self.package / "cargo/fixture/vendor/crate-1.0/LICENSE").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Cargo source checksum mismatch"):
            sources.extract(self.package, self.pin, self.destination)

    def test_source_preparation_preserves_vendored_cmake_checksums(self):
        # Only extract and edit fixture data: no configure, compiler or Cargo.
        cmake = b"add_compile_options(-Wall -Werror -Werror=return-type)\n"
        vendor = ".cargo-vendor/libgit2-sys-1.0/libgit2"
        checksums = {"files": {"libgit2/CMakeLists.txt": hashlib.sha256(cmake).hexdigest(),
                               "libgit2/modules/warnings.cmake": hashlib.sha256(cmake).hexdigest()},
                     "package": None}
        data = archive_bytes({"fixture/CMakeLists.txt": cmake, "fixture/modules/warnings.cmake": cmake,
                              f"fixture/{vendor}/CMakeLists.txt": cmake,
                              f"fixture/{vendor}/modules/warnings.cmake": cmake,
                              "fixture/.cargo-vendor/libgit2-sys-1.0/.cargo-checksum.json": json.dumps(checksums).encode()})
        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / "fixture-tag.tarball").write_bytes(data)
        pins = self.root / "deps.txt"
        pins.write_text(f"fixture|tag|https://example.invalid/fixture|{hashlib.sha256(data).hexdigest()}|MIT\n")
        subprocess.run(["sh", "-eu", "-c",
                        'HERE="$1"; PINS="$2"; SRC="$3"; DL="$4"; . "$HERE/lib.sh"; fetch fixture',
                        "fixture", str(sources.ROOT / "scripts"), str(pins), str(self.destination.parent), str(downloads)],
                       env=os.environ | {"SOURCE_PACKAGE": ""}, check=True, capture_output=True)
        expected = b"add_compile_options(-Wall -Werror=return-type)\n"
        self.assertEqual((self.destination / "CMakeLists.txt").read_bytes(), expected)
        self.assertEqual((self.destination / "modules/warnings.cmake").read_bytes(), expected)
        crate = self.destination / ".cargo-vendor/libgit2-sys-1.0"
        for name, checksum in json.loads((crate / ".cargo-checksum.json").read_text())["files"].items():
            self.assertEqual(sources.digest(crate / name), checksum, name)

    def test_collect_uses_commit_snapshot_and_excludes_maintenance(self):
        project = self.root / "project"
        for name in sources.ROOT_FILES:
            file = project / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("project fixture\n")
        (project / "scripts/maintenance").mkdir()
        (project / "scripts/maintenance/secret.txt").write_text("excluded")
        output = self.root / "sources.tar.xz"
        info = self.root / "toolchain-versions.txt"
        info.write_text("toolchain")

        def git(command, **kwargs):
            if "ls-files" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"scripts/maintenance/secret.txt\0")
            if "archive" in command:
                self.assertEqual(command[-1], self.pin.commit)
                kwargs["stdout"].write(self.archive)
                return subprocess.CompletedProcess(command, 0)
            return subprocess.CompletedProcess(command, 0, stdout=("a" * 40 if command[2] == str(project) else "b" * 40) + "\n")

        with patch.object(sources.subprocess, "run", side_effect=git), contextlib.redirect_stdout(io.StringIO()):
            sources.collect(project, {"fixture": self.pin}, self.root / "src", self.root, output, [info])
        with tarfile.open(output) as archive:
            names = archive.getnames()
            self.assertFalse(any("maintenance" in name for name in names))
            manifest = json.load(archive.extractfile(sources.BUNDLE_NAME + "/manifest.json"))
            self.assertEqual(manifest["packages"]["fixture"]["sourcePin"], self.pin.checksum)
            self.assertEqual(archive.extractfile(sources.BUNDLE_NAME + "/upstream/fixture.tar").read(), self.archive)
            self.assertEqual(archive.extractfile(sources.BUNDLE_NAME + "/project/SOURCE-CHANGES.md").read(),
                             (project / "SOURCE-CHANGES.md").read_bytes())
        with patch.object(sources.subprocess, "run", side_effect=git), self.assertRaisesRegex(ValueError, "source commit mismatch"):
            bad = Pin(self.pin.name, self.pin.version, self.pin.url, "COMMIT=" + "c" * 40, "MIT")
            sources.collect(project, {"fixture": bad}, self.root / "src", self.root, output, [info])

    def test_collect_includes_locked_cargo_sources_and_propagates_vendor_failure(self):
        source = self.root / "src/fixture"
        source.mkdir(parents=True)
        (source / "Cargo.toml").write_text("[package]\nname='fixture'\n")
        (source / "Cargo.lock").write_text("version = 4\n")
        snapshot = archive_bytes({"fixture/Cargo.toml": (source / "Cargo.toml").read_bytes(),
                                  "fixture/Cargo.lock": (source / "Cargo.lock").read_bytes()})

        def command(argv, **kwargs):
            if argv[0] == "cargo":
                vendor = Path(argv[-1])
                (vendor / "crate-1.0").mkdir(parents=True)
                (vendor / "crate-1.0/LICENSE").write_text("license")
                (vendor / "crate-1.0/.cargo-checksum.json").write_text("{}")
                return subprocess.CompletedProcess(argv, 0, stdout=f'[source.vendored-sources]\ndirectory="{vendor}"\n')
            if "archive" in argv:
                kwargs["stdout"].write(snapshot)
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 0, stdout="b" * 40 + "\n")

        output = self.root / "cargo-sources.tar.xz"
        with patch.object(sources, "project_files", return_value=[]), \
                patch.object(sources.subprocess, "run", side_effect=command), contextlib.redirect_stdout(io.StringIO()):
            sources.collect(self.root, {"fixture": self.pin}, self.root / "src", self.root, output, [])
        with tarfile.open(output) as archive:
            config = archive.extractfile(sources.BUNDLE_NAME + "/cargo/fixture/config.toml").read().decode()
            self.assertIn('directory=".cargo-vendor"', config)
            self.assertTrue(archive.getmember(sources.BUNDLE_NAME + "/cargo/fixture/vendor/crate-1.0/LICENSE").isfile())
        def failure(argv, **kwargs):
            if argv[0] == "cargo":
                raise subprocess.CalledProcessError(1, argv, stderr="vendor failed")
            return command(argv, **kwargs)
        with patch.object(sources, "project_files", return_value=[]), \
                patch.object(sources.subprocess, "run", side_effect=failure), self.assertRaises(subprocess.CalledProcessError):
            sources.collect(self.root, {"fixture": self.pin}, self.root / "src", self.root, self.root / "failed.tar.xz", [])
        self.assertFalse((self.root / "failed.tar.xz").exists())

    def test_collect_rejects_corrupt_tarball(self):
        pin = Pin("fixture", "tag", self.pin.url, "c" * 64, "MIT")
        pin.archive(self.root).write_bytes(self.archive)
        with patch.object(sources, "project_files", return_value=[]), \
                patch.object(sources.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="a" * 40)), \
                self.assertRaisesRegex(ValueError, "source checksum mismatch"):
            sources.collect(self.root, {"fixture": pin}, self.root, self.root, self.root / "sources.tar.xz", [])


if __name__ == "__main__":
    unittest.main()
