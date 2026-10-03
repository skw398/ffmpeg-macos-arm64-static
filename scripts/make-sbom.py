#!/usr/bin/env python3
"""make-sbom.py — generate an SPDX 2.3 SBOM for a release from deps.txt.

The release is a statically linked ffmpeg, so the pinned dependencies are part of
the artifact. This lists them (name, version, license, source, checksum) so a
consumer can see what is inside, and so the release can carry an SBOM
attestation (see docs/VERSION-UPDATES.md).

Usage: python3 scripts/make-sbom.py --name <artifact name> --out <file>
"""
import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, "deps.txt")
REPO = "https://github.com/skw398/ffmpeg-macos-arm64-static"


def read_deps(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not re.match(r"^[A-Za-z0-9]", line):
                continue
            parts = (line.rstrip("\n").split("|") + [""] * 6)[:5]
            rows.append({"name": parts[0], "version": parts[1], "url": parts[2],
                         "checksum": parts[3], "license": parts[4]})
    return rows


def spdx_id(name):
    return "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deps", default=DEPS)
    ap.add_argument("--name", default="ffmpeg-macos-arm64-static")
    ap.add_argument("--out", default="-", help="output file ('-' = stdout)")
    args = ap.parse_args()

    rows = read_deps(args.deps)
    if not rows:
        print("ERROR: no dependencies found", file=sys.stderr)
        return 1

    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    main_pkg = rows[0]
    packages = []
    relationships = []
    for r in rows:
        pkg = {
            "SPDXID": spdx_id(r["name"]),
            "name": r["name"],
            "versionInfo": r["version"],
            "downloadLocation": r["url"] or "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": r["license"] or "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        if r["checksum"].startswith("COMMIT="):
            pkg["sourceInfo"] = r["checksum"]
        elif re.fullmatch(r"[0-9a-fA-F]{64}", r["checksum"]):
            pkg["checksums"] = [{"algorithm": "SHA256", "checksumValue": r["checksum"].lower()}]
        packages.append(pkg)
        if r is not main_pkg:
            relationships.append({
                "spdxElementId": spdx_id(main_pkg["name"]),
                "relatedSpdxElement": spdx_id(r["name"]),
                "relationshipType": "CONTAINS",
            })

    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": args.name,
        "documentNamespace": f"{REPO}/spdx/{args.name}",
        "creationInfo": {"created": created, "creators": ["Tool: make-sbom.py"]},
        "packages": packages,
        "relationships": [{
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": spdx_id(main_pkg["name"]),
            "relationshipType": "DESCRIBES",
        }] + relationships,
    }

    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"== sbom: {args.out} ({len(rows)} package(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
