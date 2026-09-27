#!/bin/sh
# lib.sh — shared helpers for pin lookup and dependency fetch.
# Sourced by build-deps.sh and build-chromaprint.sh; the caller must set
# PINS, DL and SRC before sourcing.

# pin <name> -> "version|url|hash" from the pin list
pin() {
  awk -F'|' -v n="$1" '$1 == n { print $2 "|" $3 "|" $4; exit }' "$PINS"
}

# download <name> -> ensure the pinned tarball is present in $DL and verified,
# without extracting it (tarball pins only; commit pins are a no-op)
download() {
  name="$1"
  p=$(pin "$name")
  [ -n "$p" ] || { echo "ERROR: no pin for $name" >&2; exit 1; }
  version=$(printf '%s' "$p" | cut -d'|' -f1)
  url=$(printf '%s' "$p" | cut -d'|' -f2)
  hash=$(printf '%s' "$p" | cut -d'|' -f3)
  case "$hash" in
    COMMIT=*) return 0 ;;
  esac
  file="$DL/$name-$version.tarball"
  # a stale cached tarball (e.g. an archive regenerated upstream, or a cache
  # from a previous URL) must not be trusted: re-download on hash mismatch
  if [ -f "$file" ] && [ "$(shasum -a 256 "$file" | cut -d' ' -f1)" != "$hash" ]; then
    echo "== re-downloading $name (cached tarball hash mismatch)" >&2
    rm -f "$file"
  fi
  if [ ! -f "$file" ]; then
    curl -sSL --fail --max-time 300 -o "$file.tmp" "$url"
    mv "$file.tmp" "$file"
  fi
  actual=$(shasum -a 256 "$file" | cut -d' ' -f1)
  [ "$actual" = "$hash" ] || { echo "ERROR: $name hash mismatch" >&2; exit 1; }
}

# fetch <name> -> verify and extract (or clone) into $SRC/<name>
fetch() {
  name="$1"
  p=$(pin "$name")
  [ -n "$p" ] || { echo "ERROR: no pin for $name" >&2; exit 1; }
  version=$(printf '%s' "$p" | cut -d'|' -f1)
  url=$(printf '%s' "$p" | cut -d'|' -f2)
  hash=$(printf '%s' "$p" | cut -d'|' -f3)
  dest="$SRC/$name"

  case "$hash" in
    COMMIT=*)
      sha=${hash#COMMIT=}
      [ -d "$dest/.git" ] || git clone -q --depth 1 --branch "$version" "$url" "$dest"
      actual=$(git -C "$dest" rev-parse HEAD)
      [ "$actual" = "$sha" ] || { echo "ERROR: $name commit mismatch" >&2; exit 1; }
      ;;
    *)
      download "$name"
      file="$DL/$name-$version.tarball"
      rm -rf "$dest"; mkdir -p "$dest"
      tar -xf "$file" -C "$dest" --strip-components=1
      ;;
  esac

  # generic: drop -Werror from CMake build files; clang trips on the GCC-targeted
  # warnings these projects enable with -Wall (which -Werror then promotes)
  find "$dest" -maxdepth 5 -type f \( -name 'CMakeLists.txt' -o -name '*.cmake' \) \
    -exec perl -pi -e 's/ ?-Werror(?:=[A-Za-z0-9_-]+)?//g' {} + 2>/dev/null || true
}
