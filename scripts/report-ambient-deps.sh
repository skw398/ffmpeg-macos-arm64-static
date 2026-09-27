#!/bin/sh
# report-ambient-deps.sh — list the inputs that are resolved from OUTSIDE the
# prefix (Homebrew / system), i.e. ambient dependencies that make the build
# non-hermetic. Read-only: it inspects the restored prefix and does not change
# the build.
#
# For every module required by a .pc file in the prefix, it asks pkg-config
# (with the same PKG_CONFIG_PATH the build uses) which .pc actually answers and
# reports it when that file lives outside the prefix.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
PREFIX="${PREFIX:-$ROOT/build/prefix}"
PCDIR="$PREFIX/lib/pkgconfig"

if [ ! -d "$PCDIR" ]; then
  echo "no pkg-config dir in the prefix ($PCDIR); nothing to report"
  exit 0
fi

# same search path as the build: prefix first, then the ambient defaults
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"

echo "== prefix .pc files embedding /opt/homebrew paths =="
grep -rl '/opt/homebrew' "$PCDIR" 2>/dev/null || echo "  (none)"

echo "== modules required by the prefix that resolve OUTSIDE the prefix =="
for pc in "$PCDIR"/*.pc; do
  [ -f "$pc" ] || continue
  reqs=$(sed -n 's/^Requires\(\.private\)\{0,1\}:[[:space:]]*//p' "$pc" \
    | tr ',' '\n' | tr ' ' '\n' \
    | grep -vE '^$|^(>=|<=|=|>|<)|^[0-9]' | sort -u)
  for r in $reqs; do
    dir=$(pkg-config --variable=pcfiledir "$r" 2>/dev/null || true)
    if [ -z "$dir" ]; then
      echo "  MISSING  $r  (required by $(basename "$pc"))"
    else
      case "$dir" in
        "$PREFIX"/*) : ;;
        *) echo "  AMBIENT  $r  ->  $dir  (required by $(basename "$pc"))" ;;
      esac
    fi
  done
done | sort -u
