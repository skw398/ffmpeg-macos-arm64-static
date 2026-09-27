#!/bin/sh
# lint-workarounds.sh — report workarounds that may no longer be needed.
#
# Report only: it never fails the build. Each section names the workaround it
# tracks in docs/BUILD-WORKAROUNDS.md and says what to remove when it fires.
#
#   [C] SDK now ships libxml-2.0.pc / zlib.pc        -> drop the prefix shim
#   [C] FFmpeg no longer asks for -lstdc++           -> drop the libc++ alias
#   [D] no CMake project declares < 3.5              -> drop CMAKE_POLICY_VERSION_MINIMUM
#   [I] an artifact-consumers skip is unnecessary    -> drop the skip entry
#   [J] python3 is no longer PEP 668 managed         -> drop the meson venv
#   [H] a custom-built library gained a build system -> consider switching to it
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
SRC="${SRC:-$ROOT/build/src}"
DL="${DL:-$ROOT/build/downloads}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
BIN="$PREFIX/bin/ffmpeg"

fired=0

# --- [C] SDK pkg-config files (libxml2 / zlib shims) --------------------------
echo "== [C] SDK pkg-config files (libxml-2.0.pc / zlib.pc shims) =="
sdk=$(xcrun --show-sdk-path 2>/dev/null || true)
hit=""
if [ -n "$sdk" ]; then
  for pc in libxml-2.0.pc zlib.pc; do
    for d in "$sdk/usr/lib/pkgconfig" "$sdk/usr/share/pkgconfig"; do
      if [ -f "$d/$pc" ]; then hit="$hit $pc"; fi
    done
  done
fi
if [ -n "$hit" ]; then
  echo "  now provided by the SDK:$hit -> drop the matching shim in build-deps.sh"
  fired=1
else
  echo "  (still absent: shims still needed)"
fi

# --- [C] FFmpeg -lstdc++ (libstdc++.tbd alias) --------------------------------
echo
echo "== [C] FFmpeg -lstdc++ requirement (libstdc++.tbd alias) =="
ver=$(awk -F'|' '$1=="ffmpeg"{print $2; exit}' "$ROOT/deps.txt")
tb="$DL/ffmpeg-$ver.tarball"
if [ -f "$tb" ]; then
  if tar -xOf "$tb" "ffmpeg-$ver/configure" 2>/dev/null | grep -q -- '-lstdc++'; then
    echo "  ffmpeg configure still asks for -lstdc++: alias still needed"
  else
    echo "  ffmpeg configure no longer asks for -lstdc++ -> drop the libstdc++.tbd alias"
    fired=1
  fi
else
  echo "  (no pinned ffmpeg tarball at $tb: skipped)"
fi

# --- [D] cmake_minimum_required < 3.5 (CMAKE_POLICY_VERSION_MINIMUM) ----------
echo
echo "== [D] CMake projects declaring < 3.5 (CMAKE_POLICY_VERSION_MINIMUM) =="
low=""
scanned=0
for name in $(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$HERE/build-deps.txt" | awk -F'|' '$2=="cmake"{print $1}'); do
  src="$SRC/$name"
  [ -d "$src" ] || continue
  scanned=$((scanned + 1))
  vs=$(find "$src" -maxdepth 3 -name CMakeLists.txt \
        -exec grep -hoE 'cmake_minimum_required\([[:space:]]*VERSION[[:space:]]+[0-9]+\.[0-9]+' {} + 2>/dev/null \
        | grep -oE '[0-9]+\.[0-9]+$' | sort -u)
  for v in $vs; do
    if awk -v a="$v" 'BEGIN{ split(a,x,"."); exit !(x[1]+0 < 3 || (x[1]+0 == 3 && x[2]+0 < 5)) }'; then
      low="$low $name($v)"
      break
    fi
  done
done
if [ "$scanned" -eq 0 ]; then
  echo "  (no CMake sources present: skipped)"
elif [ -n "$low" ]; then
  echo "  still declaring < 3.5:$low"
  echo "  -> CMAKE_POLICY_VERSION_MINIMUM=3.5 still needed"
else
  echo "  none of the $scanned CMake projects declares < 3.5"
  echo "  -> CMAKE_POLICY_VERSION_MINIMUM=3.5 can be dropped"
  fired=1
fi

# --- [I] artifact-consumers skips --------------------------------------------
echo
echo "== [I] artifact-consumers skips (would the plain mapping work now?) =="
if [ -x "$BIN" ]; then
  skips=$(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$HERE/artifact-consumers.txt" 2>/dev/null | awk -F'|' '$2=="skip"{print $1}')
  if [ -n "$skips" ]; then
    for lib in $skips; do
      tmp=$(mktemp)
      grep -vE "^$lib\|" "$HERE/artifact-consumers.txt" > "$tmp"
      if OVERRIDES="$tmp" sh "$HERE/lib-consumers.sh" --check --binary "$BIN" "$lib" >/dev/null 2>&1; then
        echo "  $lib: the plain mapping now works -> remove the skip entry"
        fired=1
      else
        echo "  $lib: still needs the skip"
      fi
      rm -f "$tmp"
    done
  else
    echo "  (no skip entries)"
  fi
else
  echo "  (no ffmpeg binary at $BIN: skipped)"
fi

# --- [J] PEP 668 (meson venv) ------------------------------------------------
echo
echo "== [J] python3 externally managed (meson venv) =="
if python3 - <<'PY' 2>/dev/null
import os, sysconfig
raise SystemExit(0 if os.path.exists(os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED")) else 1)
PY
then
  echo "  python3 is still externally managed (PEP 668): the meson venv is still needed"
else
  echo "  python3 is no longer externally managed -> the meson venv may be droppable"
  fired=1
fi

# --- [H] custom-built libraries that gained a build system -------------------
echo
echo "== [H] custom builders: did upstream gain a standard build system? =="
hit=""
for name in libgsm quirc openh264; do
  src="$SRC/$name"
  [ -d "$src" ] || continue
  for f in CMakeLists.txt meson.build configure; do
    if [ -f "$src/$f" ]; then hit="$hit $name($f)"; fi
  done
done
if [ -n "$hit" ]; then
  echo "  standard build system found:$hit -> consider switching to it"
  fired=1
else
  echo "  (none: custom builders still needed)"
fi

echo
if [ "$fired" -eq 0 ]; then
  echo "RESULT: no workaround looks removable yet (report only)"
else
  echo "RESULT: some workarounds may be removable (report only; see docs/BUILD-WORKAROUNDS.md)"
fi
