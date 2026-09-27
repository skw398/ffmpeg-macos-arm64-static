#!/bin/sh
# collect-release-assets.sh — gather dependency versions, license texts and
# build info into build/artifacts/release for a Release.
#
# Handles both SHA-256 pins (license files read from the tarball) and git pins
# (license files read from the cloned source tree). File names are prefixed with
# their original path to avoid collisions.
#
# NOTE: LGPL relink materials (objects + link commands) are NOT produced here;
# that is a separate, larger step (see docs/COMPONENT-AUDIT.md).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
DL="${DL:-$ROOT/build/downloads}"
SRC="${SRC:-$ROOT/build/src}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
OUT="${OUT:-$ROOT/build/artifacts/release}"
mkdir -p "$OUT/licenses"

# --- dependency versions ---
awk -F'|' '/^[A-Za-z0-9]/{print $1 "|" $2}' "$ROOT/deps.txt" > "$OUT/dependency-versions.txt"

licenses_from_dir() { # <dir> <dest>
  dir="$1"; dest="$2"
  [ -d "$dir" ] || return 0
  find "$dir" -maxdepth 2 -type f \
    \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'COPYRIGHT*' -o -iname 'NOTICE*' -o -iname 'PATENTS*' \) \
    2>/dev/null | while IFS= read -r p; do
      rel=${p#"$dir"/}
      cp "$p" "$dest/$(printf '%s' "$rel" | tr '/' '_')" 2>/dev/null || true
    done
}

while IFS='|' read -r name version url hash license notes; do
  case "$name" in ""|\#*) continue ;; esac
  d="$OUT/licenses/$name"; mkdir -p "$d"
  case "$hash" in
    COMMIT=*)
      licenses_from_dir "$SRC/$name" "$d" ;;
    *)
      f="$DL/$name-$version.tarball"
      [ -f "$f" ] || continue
      tar -tf "$f" 2>/dev/null \
        | grep -iE '(^|/)(COPYING|LICENSE|COPYRIGHT|NOTICE|PATENTS)(\.[A-Za-z0-9]+)?$' \
        | while IFS= read -r p; do
            tar -xOf "$f" "$p" > "$d/$(printf '%s' "$p" | tr '/' '_')" 2>/dev/null || true
          done ;;
  esac
done < "$ROOT/deps.txt"

# --- FFmpeg build info ---
"$PREFIX/bin/ffmpeg" -version   > "$OUT/ffmpeg-version.txt" 2>&1 || true
"$PREFIX/bin/ffmpeg" -buildconf > "$OUT/buildconf.txt" 2>&1 || true

# --- source patches (PATCH-POLICY: the modified sources must be shipped) ---
if [ -d "$ROOT/patches" ]; then
  mkdir -p "$OUT/patches"
  cp -R "$ROOT/patches/." "$OUT/patches/"
  cp "$HERE/patches.txt" "$OUT/patches/patches.txt" 2>/dev/null || true
  echo "== source patches included: $(find "$OUT/patches" -type f | wc -l | tr -d ' ') file(s)"
fi

echo "== release assets collected: $OUT"
