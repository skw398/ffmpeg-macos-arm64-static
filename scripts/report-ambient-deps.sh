#!/bin/sh
# report-ambient-deps.sh — check that every module required by the prefix's
# .pc files resolves INSIDE the prefix.
#
# The build closes the pkg-config search to the prefix (PKG_CONFIG_LIBDIR), so
# any dependency that is not provided in the prefix would silently come from
# Homebrew/system. This script fails when such a dependency is found, so CI
# notifies us before a non-Apple library can be linked in.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
PREFIX="${PREFIX:-$ROOT/build/prefix}"
PCDIR="$PREFIX/lib/pkgconfig"

[ -d "$PCDIR" ] || { echo "ERROR: no pkg-config dir in the prefix ($PCDIR)"; exit 1; }

# same (closed) search path the build uses
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"

echo "== prefix .pc files embedding /opt/homebrew paths =="
grep -rl '/opt/homebrew' "$PCDIR" 2>/dev/null || echo "  (none)"

bad=$(mktemp)
trap 'rm -f "$bad"' EXIT

for pc in "$PCDIR"/*.pc; do
  [ -f "$pc" ] || continue
  # a .pc file that pkgconf skips (no Name/Description/Version) is treated as
  # absent, so an invalid shim would silently resolve from Homebrew/system;
  # catch that here rather than letting the closed search hide it.
  mod=$(basename "$pc" .pc)
  pkg-config --exists "$mod" 2>/dev/null \
    || echo "INVALID  $mod  (pkg-config cannot load $(basename "$pc"))" >> "$bad"
  reqs=$(sed -n 's/^Requires\(\.private\)\{0,1\}:[[:space:]]*//p' "$pc" \
    | tr ',' '\n' | tr ' ' '\n' \
    | grep -vE '^$|^(>=|<=|=|>|<)|^[0-9]' | sort -u)
  for r in $reqs; do
    dir=$(pkg-config --variable=pcfiledir "$r" 2>/dev/null || true)
    if [ -z "$dir" ]; then
      echo "MISSING  $r  (required by $(basename "$pc"))" >> "$bad"
    else
      case "$dir" in
        "$PREFIX"/*) : ;;
        *) echo "OUTSIDE  $r  ->  $dir  (required by $(basename "$pc"))" >> "$bad" ;;
      esac
    fi
  done
done

if [ -s "$bad" ]; then
  echo "== modules that do not resolve inside the prefix =="
  sort -u "$bad"
  echo "RESULT: FAILED (provide them in the prefix or disable the feature)"
  exit 1
fi
echo "RESULT: all required modules resolve inside the prefix"
