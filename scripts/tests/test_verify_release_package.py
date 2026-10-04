"""Reject broken final packages using synthetic archives; never execute binaries."""

import contextlib
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_data import DATA, ROOT, read_pins
from release_sources import BUNDLE_NAME, digest, project_files
sbom = importlib.import_module("make-sbom")
verify = importlib.import_module("verify-release-package")


class ReleasePackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.name = "ffmpeg-macos-arm64-static-v1.0-1"
        self.archive = self.folder / (self.name + ".tar.xz")
        self.sources = self.folder / (self.name + "-sources.tar.xz")
        self.sbom = self.folder / (self.name + ".spdx.json")
        self.checksums = self.folder / "SHA256SUMS"
        self.deps = self.folder / "deps.txt"
        self.deps.write_text("ffmpeg|1.0|https://example.invalid/ffmpeg|" + "a" * 64 + "|GPL-3.0-or-later\n"
                             "fixture|tag|https://example.invalid/fixture.git|COMMIT=" + "b" * 40 + "|MIT\n")
        self.entries = {
            "share/licenses/ffmpeg/COPYING": (b"ffmpeg license\n", 0o644),
            "share/licenses/fixture/LICENCE": (b"fixture license\n", 0o644),
            "share/dependency-versions.txt": (b"ffmpeg|1.0\nfixture|tag\n", 0o644),
            "share/ffmpeg-version.txt": (b"ffmpeg version 1.0\n", 0o644),
            "share/buildconf.txt": (b"--enable-gpl --enable-version3 --enable-static --disable-shared --disable-libzvbi\n", 0o644),
            "share/patches/patches.txt": ((DATA / "patches.txt").read_bytes(), 0o644),
        }
        self.licenses = self.folder / "licenses.json"
        records = {}
        for pin in read_pins(self.deps).values():
            filename = "COPYING" if pin.name == "ffmpeg" else "LICENCE"
            data = self.entries[f"share/licenses/{pin.name}/{filename}"][0]
            records[pin.name] = {"version": pin.version, "checksum": pin.checksum,
                                 "expression": pin.license, "comment": "fixture declaration",
                                 "licenseFiles": [filename],
                                 "evidence": {filename: hashlib.sha256(data).hexdigest()}}
        self.licenses.write_text(json.dumps({"licenseListVersion": "3.29.0", "licenseIds": ["MIT", "GPL-3.0-or-later"],
                                            "packages": records, "extractedLicensingInfo": []}))
        for name in ("LICENSE", "LICENSING.md", "BUILDING.md"):
            self.entries[f"share/project/{name}"] = ((ROOT / name).read_bytes(), 0o644)
        header = struct.pack("<II", 0xfeedfacf, 0x0100000c) + bytes(24)
        for binary in ("ffmpeg", "ffprobe", "ffplay"):
            self.entries["bin/" + binary] = (header, 0o755)
        for file in (ROOT / "patches").rglob("*"):
            if file.is_file():
                self.entries["share/patches/" + str(file.relative_to(ROOT / "patches"))] = (file.read_bytes(), 0o644)
        with patch.object(sys, "argv", ["make-sbom.py", "--deps", str(self.deps), "--name", self.name, "--licenses", str(self.licenses)]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(sbom.main(), 0)
        self.document = json.loads(output.getvalue())
        self.sbom.write_text(json.dumps(self.document))
        self.write_archive(self.entries)
        self.source_entries = {"project/" + name: (ROOT / name).read_bytes() for name in project_files(ROOT)}
        self.source_entries["project/deps.txt"] = self.deps.read_bytes()
        self.source_entries["project/scripts/data/licenses.json"] = self.licenses.read_bytes()
        self.source_entries["build-info/toolchain-versions.txt"] = b"fixture toolchain\n"
        self.source_entries["build-info/buildconf.txt"] = self.entries["share/buildconf.txt"][0]
        packages = {}
        for pin in read_pins(self.deps).values():
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as nested:
                info = tarfile.TarInfo(pin.name + "/LICENSE")
                info.size = 7
                nested.addfile(info, io.BytesIO(b"license"))
            filename = f"upstream/{pin.name}.tar" if pin.commit else f"upstream/{pin.name}.tarball"
            self.source_entries[filename] = data.getvalue()
            packages[pin.name] = {"version": pin.version, "url": pin.url,
                                  "sourcePin": pin.checksum, "path": filename}
        # Replace the tarball fixture's artificial pin with its real digest.
        checksum = hashlib.sha256(self.source_entries["upstream/ffmpeg.tarball"]).hexdigest()
        self.deps.write_text(self.deps.read_text().replace("a" * 64, checksum))
        licenses = json.loads(self.licenses.read_text())
        licenses["packages"]["ffmpeg"]["checksum"] = checksum
        self.licenses.write_text(json.dumps(licenses))
        packages["ffmpeg"]["sourcePin"] = checksum
        self.document["packages"][0]["checksums"][0]["checksumValue"] = checksum
        self.sbom.write_text(json.dumps(self.document))
        self.source_entries["project/deps.txt"] = self.deps.read_bytes()
        self.source_entries["project/scripts/data/licenses.json"] = self.licenses.read_bytes()
        self.source_manifest = {"format": 1, "projectCommit": "a" * 40, "packages": packages,
                                "files": {name: hashlib.sha256(data).hexdigest()
                                          for name, data in self.source_entries.items()}}
        self.write_sources(self.source_entries, self.source_manifest)

    def write_sources(self, entries, manifest):
        files = entries | {"manifest.json": json.dumps(manifest).encode()}
        with tarfile.open(self.sources, "w:xz") as archive:
            for relative, content in files.items():
                info = tarfile.TarInfo(BUNDLE_NAME + "/" + relative)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

    def write_archive(self, entries):
        with tarfile.open(self.archive, "w:xz") as archive:
            for relative, (content, mode) in entries.items():
                info = tarfile.TarInfo(self.name + "/" + relative)
                info.mode = mode
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

    def write_checksums(self):
        self.checksums.write_text("".join(hashlib.sha256(file.read_bytes()).hexdigest() + "  ./" + file.name + "\n"
                                          for file in (self.archive, self.sbom, self.sources)))

    def run_check(self):
        args = ["verify-release-package.py", "--archive", str(self.archive), "--sbom", str(self.sbom),
                "--checksums", str(self.checksums), "--sources", str(self.sources), "--deps", str(self.deps), "--licenses", str(self.licenses)]
        with patch.object(sys, "argv", args), patch.dict("os.environ", {"GITHUB_SHA": "a" * 40}), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            status = verify.main()
        return status, error.getvalue()

    def test_complete_package_passes(self):
        self.write_checksums()
        self.assertEqual(self.run_check(), (0, ""))

    def test_checksum_mismatch_and_missing_entries_fail(self):
        self.write_checksums()
        self.sbom.write_text("changed after checksums")
        status, error = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("checksum mismatch", error)
        self.write_checksums()
        self.checksums.write_text(self.checksums.read_text().splitlines()[0] + "\n")
        self.assertEqual(self.run_check(), (1, "ERROR: release package verification failed: missing release checksum\n"))

    def test_sbom_inventory_sources_licenses_and_relationships_are_checked(self):
        cases = [
            (lambda doc: doc["packages"].pop(), "inventory mismatch"),
            (lambda doc: doc["packages"].append(copy.deepcopy(doc["packages"][0])), "inventory mismatch"),
            (lambda doc: doc["packages"][0].update(versionInfo="wrong"), "version mismatch"),
            (lambda doc: doc["packages"][0]["checksums"][0].update(checksumValue="0" * 64), "checksum mismatch"),
            (lambda doc: doc["packages"][1].update(sourceInfo="COMMIT=" + "0" * 40), "commit mismatch"),
            (lambda doc: doc["packages"][0].update(licenseDeclared="freeform prose"), "license declaration mismatch"),
            (lambda doc: doc.update(documentNamespace="https://example.invalid/reused-name"), "invalid SPDX namespace"),
            (lambda doc: doc["relationships"][0].update(relatedSpdxElement="SPDXRef-unknown"), "unknown SPDX relationship"),
            (lambda doc: doc.update(hasExtractedLicensingInfos=[{"licenseId": "LicenseRef-fake", "extractedText": "fake"}]), "extracted license text mismatch"),
            (lambda doc: doc.update(relationships=[]), "dependency relationships mismatch"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                document = copy.deepcopy(self.document)
                change(document)
                self.sbom.write_text(json.dumps(document))
                self.write_checksums()
                status, error = self.run_check()
                self.assertEqual(status, 1)
                self.assertIn(message, error)

    def test_archive_omissions_and_invalid_binaries_fail(self):
        cases = [
            ("share/licenses/fixture/LICENCE", None, "missing license files: fixture"),
            ("share/licenses/fixture/LICENCE", (b"", 0o644), "invalid license file"),
            ("share/licenses/fixture/LICENCE", (b"wrong license", 0o644), "reviewed license text mismatch"),
            ("bin/ffplay", None, "missing release binary"),
            ("bin/ffmpeg", (bytes(32), 0o755), "not arm64 Mach-O"),
            ("bin/ffmpeg", (self.entries["bin/ffmpeg"][0], 0o644), "invalid release binary"),
            ("share/dependency-versions.txt", (b"ffmpeg|wrong\n", 0o644), "release metadata mismatch"),
            ("share/patches/patches.txt", None, "release metadata mismatch"),
            ("share/buildconf.txt", (b"--enable-gpl --enable-shared\n", 0o644), "missing release build flags"),
            ("share/buildconf.txt", (self.entries["share/buildconf.txt"][0] + b"--enable-libzvbi\n", 0o644), "incompatible release build flags"),
            ("share/buildconf.txt", (self.entries["share/buildconf.txt"][0] + b"--enable-nonfree\n", 0o644), "incompatible release build flags"),
            ("../outside", (b"wrong path", 0o644), "unexpected archive path"),
        ]
        patch_name = next(name for name in self.entries if name.endswith(".patch"))
        cases.append((patch_name, None, "missing source patches"))
        for relative, replacement, message in cases:
            with self.subTest(relative=relative, message=message):
                entries = self.entries.copy()
                if replacement is None:
                    del entries[relative]
                else:
                    entries[relative] = replacement
                self.write_archive(entries)
                self.write_checksums()
                status, error = self.run_check()
                self.assertEqual(status, 1)
                self.assertIn(message, error)

    def test_source_inventory_pins_contents_and_build_metadata_are_checked(self):
        changes = [
            (lambda entries, manifest: entries.pop("upstream/fixture.tar"), "source file inventory"),
            (lambda entries, manifest: entries.update({"upstream/fixture.tar": b"changed"}), "source file inventory"),
            (lambda entries, manifest: manifest["packages"].pop("fixture"), "source package inventory"),
            (lambda entries, manifest: manifest["packages"]["fixture"].update(sourcePin="COMMIT=" + "c" * 40), "source package pin mismatch"),
            (lambda entries, manifest: manifest.update(projectCommit="b" * 40), "source project commit mismatch"),
        ]
        for change, message in changes:
            with self.subTest(message=message):
                entries = self.source_entries.copy()
                manifest = copy.deepcopy(self.source_manifest)
                change(entries, manifest)
                self.write_sources(entries, manifest)
                self.write_checksums()
                status, error = self.run_check()
                self.assertEqual(status, 1)
                self.assertIn(message, error)
        for name, message in [("project/scripts/build-ffmpeg.sh", "source build scripts"),
                              ("build-info/buildconf.txt", "source and binary build configuration"),
                              ("build-info/toolchain-versions.txt", "missing source build toolchain")]:
            with self.subTest(name=name):
                entries = self.source_entries | {name: b""}
                manifest = copy.deepcopy(self.source_manifest)
                manifest["files"][name] = hashlib.sha256(b"").hexdigest()
                self.write_sources(entries, manifest)
                self.write_checksums()
                status, error = self.run_check()
                self.assertEqual(status, 1)
                self.assertIn(message, error)


if __name__ == "__main__":
    unittest.main()
