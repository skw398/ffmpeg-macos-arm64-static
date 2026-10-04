#!/usr/bin/env python3
"""Check the final release archive, SBOM and checksums without running binaries."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tarfile
import uuid

from build_data import DATA, ROOT, read_pins, read_licenses
from release_sources import BUNDLE_NAME, digest, project_files


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_checksums(path, archive, sbom, sources):
    expected = {file.name: file for file in (archive, sbom, sources)}
    seen = set()
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        require(match is not None, "invalid SHA256SUMS line")
        digest, name = match.groups()
        name = str(PurePosixPath(name))
        require(name in expected and name not in seen, f"unexpected or duplicate checksum: {name}")
        with expected[name].open("rb") as source:
            require(hashlib.file_digest(source, "sha256").hexdigest() == digest,
                    f"checksum mismatch: {name}")
        seen.add(name)
    require(seen == set(expected), "missing release checksum")


def check_sbom(path, name, pins, licenses):
    document = json.loads(path.read_text())
    require(document["spdxVersion"] == "SPDX-2.3" and document["dataLicense"] == "CC0-1.0",
            "unexpected SPDX document version or data license")
    require(document["name"] == name, "SBOM release name mismatch")
    namespace = document["documentNamespace"]
    require(namespace.startswith("https://github.com/skw398/ffmpeg-macos-arm64-static/spdx/")
            and uuid.UUID(namespace.rsplit("/", 1)[1]).version == 4, "invalid SPDX namespace")
    packages = document["packages"]
    names = [package["name"] for package in packages]
    require(len(names) == len(set(names)) and set(names) == set(pins), "SBOM package inventory mismatch")
    identifiers = {document["SPDXID"]}
    for package in packages:
        pin = pins[package["name"]]
        require(package["SPDXID"] not in identifiers, "duplicate SPDX identifier")
        identifiers.add(package["SPDXID"])
        require(package["versionInfo"] == pin.version and package["downloadLocation"] == pin.url,
                f"SBOM source or version mismatch: {pin.name}")
        if pin.commit:
            require(package["sourceInfo"] == pin.checksum, f"SBOM commit mismatch: {pin.name}")
        else:
            require(package["checksums"] == [{"algorithm": "SHA256", "checksumValue": pin.checksum.lower()}],
                    f"SBOM source checksum mismatch: {pin.name}")
        review = licenses["packages"][pin.name]
        require(package["licenseDeclared"] == review["expression"]
                and package["licenseConcluded"] == "NOASSERTION"
                and package["licenseComments"] == review["comment"], f"SBOM license declaration mismatch: {pin.name}")
    require(document["creationInfo"]["licenseListVersion"] == licenses["licenseListVersion"],
            "SPDX license list version mismatch")
    require(document.get("hasExtractedLicensingInfos") == licenses["extractedLicensingInfo"],
            "SBOM extracted license text mismatch")
    for relationship in document["relationships"]:
        require(relationship["spdxElementId"] in identifiers
                and relationship["relatedSpdxElement"] in identifiers, "unknown SPDX relationship endpoint")
    package_ids = {package["name"]: package["SPDXID"] for package in packages}
    expected_relationships = {(document["SPDXID"], package_ids["ffmpeg"], "DESCRIBES")} | {
        (package_ids["ffmpeg"], identifier, "CONTAINS") for package, identifier in package_ids.items()
        if package != "ffmpeg"}
    actual_relationships = {(relation["spdxElementId"], relation["relatedSpdxElement"], relation["relationshipType"])
                            for relation in document["relationships"]}
    require(actual_relationships == expected_relationships, "SBOM dependency relationships mismatch")


def check_archive(path, name, pins, catalog):
    expected_licenses = {f"share/licenses/{package}/{filename}": review["evidence"][filename]
                         for package, review in catalog["packages"].items()
                         for filename in review["licenseFiles"]}
    found_licenses = set()
    binaries = set()
    licenses = set()
    metadata = {}
    expected_text = {
        "share/dependency-versions.txt": "".join(f"{pin.name}|{pin.version}\n" for pin in pins.values()),
        "share/patches/patches.txt": (DATA / "patches.txt").read_text(),
        **{f"share/project/{name}": (ROOT / name).read_text()
           for name in ("LICENSE", "LICENSING.md", "BUILDING.md")},
    }
    expected_patches = {"share/patches/" + str(file.relative_to(ROOT / "patches")): file.read_bytes()
                        for file in (ROOT / "patches").rglob("*") if file.is_file()}
    found_patches = set()
    seen = set()
    with tarfile.open(path, "r|xz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            require(parts and parts[0] == name and ".." not in parts and not member.name.startswith("/"),
                    f"unexpected archive path: {member.name}")
            require(member.name not in seen, f"duplicate archive entry: {member.name}")
            seen.add(member.name)
            require(member.isdir() or member.isfile(), f"unsupported archive entry: {member.name}")
            if not member.isfile():
                continue
            relative = str(PurePosixPath(*parts[1:]))
            if relative.startswith("bin/"):
                require(relative in {"bin/ffmpeg", "bin/ffprobe", "bin/ffplay"}
                        and member.mode & 0o111 and member.size >= 32, f"invalid release binary: {relative}")
                with archive.extractfile(member) as source:
                    require(struct.unpack("<II", source.read(8)) == (0xfeedfacf, 0x0100000c),
                            f"binary is not arm64 Mach-O: {relative}")
                binaries.add(relative)
            elif relative.startswith("share/licenses/"):
                require(len(parts) >= 5 and parts[3] in pins and member.size > 0,
                        f"invalid license file: {relative}")
                licenses.add(parts[3])
                if relative in expected_licenses:
                    with archive.extractfile(member) as source:
                        require(hashlib.file_digest(source, "sha256").hexdigest() == expected_licenses[relative],
                                f"reviewed license text mismatch: {relative}")
                    found_licenses.add(relative)
            elif relative in expected_patches:
                with archive.extractfile(member) as source:
                    require(source.read() == expected_patches[relative], f"source patch mismatch: {relative}")
                found_patches.add(relative)
            elif relative in expected_text or relative in {"share/buildconf.txt", "share/ffmpeg-version.txt"}:
                with archive.extractfile(member) as source:
                    metadata[relative] = source.read().decode()
    require(binaries == {"bin/ffmpeg", "bin/ffprobe", "bin/ffplay"}, "missing release binary")
    require(licenses == set(pins), "missing license files: " + ", ".join(sorted(set(pins) - licenses)))
    require(found_licenses == set(expected_licenses), "missing reviewed license texts: "
            + ", ".join(sorted(set(expected_licenses) - found_licenses)))
    require(found_patches == set(expected_patches), "missing source patches")
    for relative, content in expected_text.items():
        require(metadata.get(relative) == content, f"release metadata mismatch: {relative}")
    require(f"ffmpeg version {pins['ffmpeg'].version}" in metadata.get("share/ffmpeg-version.txt", ""),
            "FFmpeg version metadata mismatch")
    buildconf = metadata.get("share/buildconf.txt", "").split()
    require(all(flag in buildconf for flag in ("--enable-gpl", "--enable-version3", "--enable-static", "--disable-shared", "--disable-libzvbi")),
            "missing release build flags")
    require("--enable-nonfree" not in buildconf and "--enable-libzvbi" not in buildconf,
            "incompatible release build flags")

    return metadata


def check_sources(path, pins, buildconf, deps_path, licenses_path):
    hashes = {}
    sizes = {}
    seen = set()
    manifest = None
    with tarfile.open(path, "r|xz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            require(parts and parts[0] == BUNDLE_NAME and ".." not in parts
                    and not member.name.startswith("/"), f"unexpected source archive path: {member.name}")
            require(member.name not in seen, f"duplicate source archive entry: {member.name}")
            seen.add(member.name)
            require(member.isdir() or member.isfile(), f"unsupported source archive entry: {member.name}")
            if not member.isfile():
                continue
            relative = str(PurePosixPath(*parts[1:]))
            with archive.extractfile(member) as source:
                if relative == "manifest.json":
                    manifest = json.load(source)
                else:
                    hashes[relative] = hashlib.file_digest(source, "sha256").hexdigest()
                    sizes[relative] = member.size
    require(manifest is not None and manifest["format"] == 1, "missing or invalid source manifest")
    require(hashes == manifest["files"], "source file inventory or checksum mismatch")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest["projectCommit"]) is not None,
            "invalid source project commit")
    if os.environ.get("GITHUB_SHA"):
        require(manifest["projectCommit"] == os.environ["GITHUB_SHA"], "source project commit mismatch")
    require(set(manifest["packages"]) == set(pins), "source package inventory mismatch")
    expected_project = {"project/" + name: digest(ROOT / name) for name in project_files(ROOT)}
    expected_project["project/deps.txt"] = digest(deps_path)
    expected_project["project/scripts/data/licenses.json"] = digest(licenses_path)
    actual_project = {name: value for name, value in hashes.items() if name.startswith("project/")}
    require(actual_project == expected_project, "source build scripts, recipes or patches mismatch")
    for pin in pins.values():
        record = manifest["packages"][pin.name]
        require((record["version"], record["url"], record["sourcePin"]) == (pin.version, pin.url, pin.checksum),
                f"source package pin mismatch: {pin.name}")
        filename = f"upstream/{pin.name}.tar" if pin.commit else f"upstream/{pin.name}.tarball"
        require(record["path"] == filename and sizes.get(filename, 0) > 0,
                f"missing pinned source archive: {pin.name}")
        if not pin.commit:
            require(hashes[filename] == pin.checksum.lower(), f"upstream source checksum mismatch: {pin.name}")
        if "cargo" in record:
            cargo = record["cargo"]
            require(cargo == {"vendor": f"cargo/{pin.name}/vendor", "config": f"cargo/{pin.name}/config.toml",
                              "lock": f"cargo/{pin.name}/Cargo.lock"}, f"invalid Cargo source paths: {pin.name}")
            require(sizes.get(cargo["config"], 0) > 0 and sizes.get(cargo["lock"], 0) > 0,
                    f"missing Cargo configuration or lock: {pin.name}")
            require(any(name.startswith(cargo["vendor"] + "/") and name.endswith("/.cargo-checksum.json")
                        for name in hashes), f"missing vendored Cargo sources: {pin.name}")
    require(hashes.get("build-info/buildconf.txt") == hashlib.sha256(buildconf.encode()).hexdigest(),
            "source and binary build configuration mismatch")
    require(sizes.get("build-info/toolchain-versions.txt", 0) > 0, "missing source build toolchain record")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--checksums", type=Path, default=Path("SHA256SUMS"))
    parser.add_argument("--licenses", type=Path, default=DATA / "licenses.json")
    parser.add_argument("--deps", type=Path, default=ROOT / "deps.txt")
    args = parser.parse_args()
    try:
        pins = read_pins(args.deps)
        require(all(pin.commit or re.fullmatch(r"[0-9a-fA-F]{64}", pin.checksum) for pin in pins.values()),
                "unresolved source pin in release")
        licenses = read_licenses(pins, args.licenses)
        name = args.archive.name.removesuffix(".tar.xz")
        check_checksums(args.checksums, args.archive, args.sbom, args.sources)
        check_sbom(args.sbom, name, pins, licenses)
        metadata = check_archive(args.archive, name, pins, licenses)
        check_sources(args.sources, pins, metadata["share/buildconf.txt"], args.deps, args.licenses)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(f"ERROR: release package verification failed: {error}", file=sys.stderr)
        return 1
    print(f"RESULT: release package verified ({len(pins)} dependencies, licenses, SBOM, checksums, arm64 binaries, patches and sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
