"""Shared readers for the build's data files and source archives."""

from dataclasses import dataclass
import os
import json
from pathlib import Path
import re
import tarfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"


def env_path(name, default):
    return Path(os.environ.get(name) or default)


def rows(path):
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield [field.strip() for field in line.split("|")]


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    url: str
    checksum: str
    license: str
    notes: str = ""

    @property
    def commit(self):
        return self.checksum.removeprefix("COMMIT=") if self.checksum.startswith("COMMIT=") else ""

    def archive(self, downloads):
        return Path(downloads) / f"{self.name}-{self.version}.tarball"


@dataclass(frozen=True)
class Recipe:
    name: str
    kind: str
    flags: str
    note: str = ""
    subdir: str = ""


def read_pins(path=ROOT / "deps.txt"):
    pins = {}
    for row in rows(path):
        if not 5 <= len(row) <= 6 or not all(row[:4]):
            raise ValueError(f"{path}: pin requires name, version, URL, checksum and license column")
        pin = Pin(*(row + [""] * (6 - len(row))))
        if pin.name in pins:
            raise ValueError(f"{path}: duplicate pin: {pin.name}")
        if not (re.fullmatch(r"[0-9a-fA-F]{64}|COMMIT=[0-9a-f]{40}", pin.checksum)
                or pin.checksum in {"HASH-TODO", "PIN-IN-CI"}):
            raise ValueError(f"{path}: invalid checksum pin: {pin.name}")
        pins[pin.name] = pin
    if not pins:
        raise ValueError(f"{path}: no dependency pins")
    return pins


def license_identifiers(expression, known):
    """Parse the AND/OR expressions used by the reviewed license inventory."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.-]*|[()]", expression)
    if "".join(tokens) != re.sub(r"\s", "", expression):
        raise ValueError(f"invalid license expression: {expression}")
    identifiers = set()
    position = 0

    def term():
        nonlocal position
        if position >= len(tokens):
            raise ValueError(f"incomplete license expression: {expression}")
        token = tokens[position]
        position += 1
        if token == "(":
            compound()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError(f"unclosed license expression: {expression}")
            position += 1
        elif token in known:
            identifiers.add(token)
        else:
            raise ValueError(f"unknown license identifier: {token}")

    def compound():
        nonlocal position
        term()
        while position < len(tokens) and tokens[position] in {"AND", "OR"}:
            position += 1
            term()

    compound()
    if position != len(tokens):
        raise ValueError(f"invalid license expression: {expression}")
    return identifiers


def read_licenses(pins, path=DATA / "licenses.json"):
    """Require license evidence to match the exact reviewed source pins."""
    catalog = json.loads(Path(path).read_text())
    records = catalog["packages"]
    if set(records) != set(pins):
        raise ValueError("license inventory does not match dependency pins")
    references = catalog["extractedLicensingInfo"]
    reference_ids = set()
    for reference in references:
        identifier = reference["licenseId"]
        if (not re.fullmatch(r"LicenseRef-[A-Za-z0-9.-]+", identifier)
                or identifier in reference_ids or not reference["extractedText"].strip()):
            raise ValueError(f"invalid extracted license: {identifier}")
        reference_ids.add(identifier)
    known = set(catalog["licenseIds"]) | reference_ids
    used_references = set()
    for name, pin in pins.items():
        record = records[name]
        if (record["version"], record["checksum"], record["expression"]) != (
                pin.version, pin.checksum, pin.license):
            raise ValueError(f"license review does not match source pin: {name}")
        if not record["comment"].strip():
            raise ValueError(f"missing license review comment: {name}")
        if pin.license != "NOASSERTION":
            used_references |= license_identifiers(pin.license, known) & reference_ids
        elif not record["comment"].startswith("NOASSERTION:"):
            raise ValueError(f"missing unresolved license reason: {name}")
        if not record["licenseFiles"]:
            raise ValueError(f"missing license files: {name}")
        for filename, digest in record["evidence"].items():
            file = Path(filename)
            if (file.is_absolute() or ".." in file.parts
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError(f"invalid license evidence: {name}/{filename}")
        if not set(record["licenseFiles"]) <= record["evidence"].keys():
            raise ValueError(f"missing license file evidence: {name}")
    if used_references != reference_ids:
        raise ValueError("unused extracted license declaration")
    return catalog


def read_recipes(path=DATA / "build-deps.txt"):
    return [Recipe(*((row + [""] * 5)[:5])) for row in rows(path)]


def archive_files(path, predicate=lambda name: True):
    """Read matching regular files without extracting archive paths to disk."""
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            if member.isfile() and predicate(member.name):
                with archive.extractfile(member) as source:
                    yield member.name, source.read()


def archive_text(path, name):
    with tarfile.open(path, "r:*") as archive:
        with archive.extractfile(name) as source:
            return source.read().decode("utf-8", "replace")


def source_files(directory, max_depth, predicate):
    directory = Path(directory)
    for current, subdirectories, files in os.walk(directory):
        current = Path(current)
        depth = len(current.relative_to(directory).parts)
        if depth + 1 >= max_depth:
            subdirectories.clear()
        else:
            subdirectories.sort()
        for name in sorted(files):
            if predicate(name):
                yield current / name


def is_cmake(name):
    return name == "CMakeLists.txt" or name.endswith(".cmake")


def is_meson_options(name):
    return Path(name).name in {"meson_options.txt", "meson.options"}
