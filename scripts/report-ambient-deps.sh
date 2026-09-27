#!/bin/sh
# report-ambient-deps.sh — list the inputs that are resolved from OUTSIDE the
# prefix (Homebrew / system), i.e. ambient dependencies that make the build
# non-hermetic. Read-only: it inspects the restored prefix and FFmpeg tree and
# does not change the build.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
PREFIX="${PREFIX:-$ROOT/build/prefix}"
PCDIR="$PREFIX/lib/pkgconfig"

if [ ! -d "$PCDIR" ]; then
  echo "no pkg-config dir in the prefix ($PCDIR); nothing to report"
  exit 0
fi

echo "== prefix .pc files referencing /opt/homebrew =="
grep -rl '/opt/homebrew' "$PCDIR" 2>/dev/null || echo "  (none)"

echo "== modules required by our .pc files but not visible inside the prefix =="
for pc in "$PCDIR"/*.pc; do
  [ -f "$pc" ] || continue
  reqs=$(sed -n 's/^Requires\(\.private\)\{0,1\}:[[:space:]]*//p' "$pc" | tr ',' ' ')
  for r in $reqs; do
    [ -n "$r" ] || continue
    # visible when only the prefix is searched?
    if PKG_CONFIG_LIBDIR="$PCDIR" PKG_CONFIG_PATH= pkg-config --exists "$r" 2>/dev/null; then
      continue
    fi
    if pkg-config --exists "$r" 2>/dev/null; then
      where=$(pkg-config --variable=pcfiledir "$r" 2>/dev/null)
      echo "  AMBIENT  $r  (required by $(basename "$pc"); $where)"
    else
      echo "  MISSING  $r  (required by $(basename "$pc"))"
    fi
  done
done | sort -u

echo "== FFmpeg build flags referencing /opt/homebrew =="
cfg="$ROOT/build/src/ffmpeg/ffbuild/config.mak"
if [ -f "$cfg" ]; then
  grep -oE '[-A-Za-z0-9_./]*/opt/homebrew[^ ]*' "$cfg" | sort -u || echo "  (none)"
else
  echo "  (no ffmpeg config.mak)"
fi
