#!/bin/sh
# verify-build-flags.sh — check that every flag in build-deps.txt names an option
# that actually appears in its library's pinned source. This catches typos and
# removed/renamed options before the slow CI build. It does not build anything.
#
# Heuristic: the option name must appear somewhere in the tarball. Standard CMake
# cache variables (*_DIR, CMAKE_*) are skipped.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
DL="${DL:-$ROOT/build/downloads}"
RECIPES="$HERE/build-deps.txt"
PINS="$ROOT/deps.txt"

# option name named by a build flag (empty if not applicable)
flag_option() {
  case "$1" in
    --enable-*)  printf '%s' "${1#--enable-}" ;;
    --disable-*) printf '%s' "${1#--disable-}" ;;
    --with-*)    printf '%s' "${1#--with-}" ;;
    --without-*) printf '%s' "${1#--without-}" ;;
    -D*)         printf '%s' "${1#-D}" | cut -d= -f1 ;;
    *)           printf '' ;;
  esac
}

recipes_tmp=$(mktemp); content_tmp=$(mktemp)
trap 'rm -f "$recipes_tmp" "$content_tmp"' EXIT
grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$RECIPES" > "$recipes_tmp"

fail=0
while IFS='|' read -r name kind flags note; do
  name=$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  kind=$(printf '%s' "$kind" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  flags=$(printf '%s' "$flags" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [ -n "$name" ] || continue
  case "$kind" in cmake|meson|autotools) ;; *) continue ;; esac

  ver=$(awk -F'|' -v n="$name" '$1 == n { print $2; exit }' "$PINS")
  hash=$(awk -F'|' -v n="$name" '$1 == n { print $4; exit }' "$PINS")
  case "$hash" in COMMIT=*|"") continue ;; esac
  f="$DL/$name-$ver.tarball"
  [ -f "$f" ] || { echo "skip $name (missing tarball)"; continue; }

  tar -xOf "$f" > "$content_tmp" 2>/dev/null || true
  for fl in $flags; do
    opt=$(flag_option "$fl")
    [ -n "$opt" ] || continue
    case "$opt" in *_DIR|CMAKE_*) continue ;; esac
    if grep -qF -- "$opt" "$content_tmp"; then
      :
    else
      echo "INVALID $name: $fl ('$opt' not found in source)"; fail=1
    fi
  done
done < "$recipes_tmp"

if [ "$fail" -ne 0 ]; then echo "RESULT: build flag lint FAILED"; exit 1; fi
echo "RESULT: build flag lint passed"
