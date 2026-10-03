"""Shared readers for the build's data files and source archives."""

from dataclasses import dataclass
import os
from pathlib import Path
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
    return {row[0]: Pin(*((row + [""] * 6)[:6])) for row in rows(path)}


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
