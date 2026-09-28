#!/bin/sh
# lint-workarounds.sh — report workarounds that may no longer be needed.
#
# Report only: it never fails the build. Each section names the workaround it
# tracks in docs/BUILD-WORKAROUNDS.md and says what to remove when it fires.
#
#   [B] no CMake source adds -Werror                 -> drop the CMake -Werror strip
#   [C] SDK now ships libxml-2.0.pc / zlib.pc        -> drop the prefix shim
#   [C] FFmpeg no longer asks for -lstdc++           -> drop the libc++ alias
#   [D] no CMake project declares < 3.5              -> drop CMAKE_POLICY_VERSION_MINIMUM
#   [I] an artifact-consumers skip is unnecessary    -> drop the skip entry
#   [J] python3 is no longer PEP 668 managed         -> drop the meson venv
#   [H] a custom-built library gained a build system -> consider switching to it
#   [T] a trial id is listed but never consulted     -> the trial does nothing
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
SRC="${SRC:-$ROOT/build/src}"
DL="${DL:-$ROOT/build/downloads}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
PINS="$ROOT/deps.txt"
BIN="$PREFIX/bin/ffmpeg"

fired=0

# projects we build: the recipes in build-deps.txt, plus chromaprint, which is
# built outside that file (between the FFmpeg passes) but gets the same flags
all_projects() {
  grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$HERE/build-deps.txt" | awk -F'|' '{print $1}'
  echo chromaprint
}

# the subset built with CMake (for the -D flag checks)
cmake_projects() {
  grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$HERE/build-deps.txt" | awk -F'|' '$2=="cmake"{print $1}'
  echo chromaprint
}

# --- [B] CMake sources adding -Werror (the CMake -Werror strip) ---------------
# lib.sh rewrites the working tree before configure, so this must read the
# PRISTINE sources: the committed files for git pins, the pinned tarball for
# SHA-256 pins. Scanning the working tree always finds nothing, which is exactly
# the false negative that let the strip be dropped once. The pattern is what the
# strip removes: a bare -Werror. -Werror=<w> / -Werror-<w> are kept (they are
# specific diagnostics, often passed as check_c_compiler_flag arguments, where
# removing them would corrupt the CMake code).
echo "== [B] CMake sources adding -Werror (the CMake -Werror strip) =="
werror_pat=' ?-Werror([^=A-Za-z0-9_,-]|$)'
hits=""
scanned=0
for name in $(all_projects); do
  src="$SRC/$name"
  ver=$(awk -F'|' -v n="$name" '$1 == n { print $2; exit }' "$PINS")
  hash=$(awk -F'|' -v n="$name" '$1 == n { print $4; exit }' "$PINS")
  case "$hash" in
    COMMIT=*)
      [ -d "$src/.git" ] || continue
      scanned=$((scanned + 1))
      h=$(git -C "$src" grep -lE "$werror_pat" HEAD -- '*CMakeLists.txt' '*.cmake' 2>/dev/null \
            | sed "s|^HEAD:||;s|^|$name/|" | tr '\n' ' ')
      ;;
    *)
      tb="$DL/$name-$ver.tarball"
      [ -f "$tb" ] || continue
      scanned=$((scanned + 1))
      h=$(tar -tf "$tb" 2>/dev/null | grep -E '(^|/)(CMakeLists\.txt|[^/]*\.cmake)$' \
            | while IFS= read -r p; do
                tar -xOf "$tb" "$p" 2>/dev/null | grep -qE "$werror_pat" \
                  && printf '%s/%s\n' "$name" "$p"
              done | tr '\n' ' ')
      ;;
  esac
  [ -n "$h" ] && hits="$hits $h"
done
if [ "$scanned" -eq 0 ]; then
  echo "  (no pinned sources present: skipped)"
elif [ -n "$hits" ]; then
  echo "  still adding a bare -Werror:$hits"
  echo "  -> the CMake -Werror strip is still needed"
else
  echo "  none of the $scanned project(s) adds a bare -Werror"
  echo "  -> the CMake -Werror strip can be dropped"
  fired=1
fi

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
for name in $(cmake_projects); do
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

# --- [D] CMake projects that never read BUILD_TESTING ------------------------
# -DBUILD_TESTING=OFF is passed to every CMake project. The triage counts the
# libraries whose log reports it unused (labelled [global]), but chromaprint
# builds after that step, so its log is never counted: check the sources here.
echo
echo "== [D] CMake projects that never read BUILD_TESTING (-DBUILD_TESTING=OFF) =="
reads=""
never=""
for name in $(cmake_projects); do
  src="$SRC/$name"
  [ -d "$src" ] || continue
  if find "$src" -maxdepth 3 \( -name CMakeLists.txt -o -name '*.cmake' \) \
       -exec grep -qE 'BUILD_TESTING|include\([[:space:]]*CTest' {} + 2>/dev/null; then
    reads="$reads $name"
  else
    never="$never $name"
  fi
done
if [ -z "$reads$never" ]; then
  echo "  (no CMake sources present: skipped)"
elif [ -z "$reads" ]; then
  echo "  no CMake project reads BUILD_TESTING:$never"
  echo "  -> -DBUILD_TESTING=OFF can be dropped"
  fired=1
else
  echo "  reads BUILD_TESTING:$reads"
  [ -n "$never" ] && echo "  never reads it (the flag is inert there):$never"
  echo "  -> -DBUILD_TESTING=OFF still needed"
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

# --- [T] trial ids: is every listed id consulted? ----------------------------
echo
echo "== [T] trial workaround ids (trial-workarounds.txt vs the scripts) =="
trials="$HERE/trial-workarounds.txt"
scanned="$HERE/build-deps.sh $HERE/build-ffmpeg.sh"
listed=$(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$trials" | cut -d'|' -f1 | sort -u)
consulted=$(grep -hoE 'trial_(disabled|flag) [a-z0-9-]+' $scanned | awk '{print $2}' | sort -u)
unused=$(printf '%s\n' "$listed" | grep -vE '^$' | grep -vxF "$consulted" | tr '\n' ' ' || true)
unlisted=$(printf '%s\n' "$consulted" | grep -vE '^$' | grep -vxF "$listed" | tr '\n' ' ' || true)
if [ -n "$unused" ] || [ -n "$unlisted" ]; then
  if [ -n "$unused" ]; then
    echo "  listed but never consulted:$unused"
    echo "  -> the trial would silently do nothing; wire it up or drop the id"
  fi
  if [ -n "$unlisted" ]; then
    echo "  consulted but not listed:$unlisted -> add it to trial-workarounds.txt"
  fi
else
  echo "  every listed id is consulted, and every consulted id is listed"
fi

echo
if [ "$fired" -eq 0 ]; then
  echo "RESULT: no workaround looks removable yet (report only)"
else
  echo "RESULT: some workarounds may be removable (report only; see docs/BUILD-WORKAROUNDS.md)"
fi
