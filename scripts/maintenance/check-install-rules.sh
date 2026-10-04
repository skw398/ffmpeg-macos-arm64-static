#!/bin/sh
# check-install-rules.sh — can libgsm's and quirc's own install rules replace the
# copy that build-deps.sh does for them?
#
# Run it after the sources are present, e.g.:
#   ONLY="libgsm quirc" sh scripts/build-deps.sh
#   sh scripts/maintenance/check-install-rules.sh
#
# Report only: it never fails, so it can run as a dedicated job that tells us
# when a copy workaround became unnecessary.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/../.."
SRC="${SRC:-$ROOT/build/src}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fired=0

# libgsm: its install rule must put the header where FFmpeg looks (include/,
# not inc/) and must not need the caller to pass INSTALL_ROOT.
echo "== libgsm install rule =="
if [ -d "$SRC/libgsm" ]; then
  if ( cd "$SRC/libgsm" && make install INSTALL_ROOT="$tmp/gsm" ) >/dev/null 2>&1 \
     && [ -f "$tmp/gsm/include/gsm.h" ]; then
    echo "  make install works and puts gsm.h in include/: the copy can be dropped"
    fired=1
  else
    echo "  still needs the copy (INSTALL_ROOT / header goes to inc/)"
  fi
else
  echo "  (no libgsm source present: skipped)"
fi

# quirc: its install rule must work unprivileged and without building the demo
# binaries (they need SDL, which we do not have).
echo
echo "== quirc install rule =="
if [ -d "$SRC/quirc" ]; then
  if ( cd "$SRC/quirc" && make install PREFIX="$tmp/quirc" ) >/dev/null 2>&1; then
    echo "  make install works unprivileged without the demos: the copy can be dropped"
    fired=1
  else
    echo "  still needs the copy (root-only install / demo dependency)"
  fi
else
  echo "  (no quirc source present: skipped)"
fi

echo
if [ "$fired" -eq 0 ]; then
  echo "RESULT: both copies are still needed (report only)"
else
  echo "RESULT: at least one copy can be replaced by the upstream install rule"
fi
