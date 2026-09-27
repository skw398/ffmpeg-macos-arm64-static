#!/bin/sh
# build-chromaprint.sh — build chromaprint against an FFmpeg pass-1 install.
#
# chromaprint links FFmpeg's own libraries, so it cannot be built with the
# other deps. Order: build-deps.sh -> build-ffmpeg.sh --no-chromaprint ->
# build-chromaprint.sh -> build-ffmpeg.sh (full).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
DL="${DL:-$ROOT/build/downloads}"
SRC="${SRC:-$ROOT/build/src}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
PINS="$ROOT/deps.txt"

export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"

. "$HERE/lib.sh"
fetch chromaprint

src="$SRC/chromaprint"

cmake -S "$src" -B "$src/.build" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTS=OFF -DBUILD_TOOLS=OFF \
  -DCMAKE_PREFIX_PATH="$PREFIX" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET"
cmake --build "$src/.build" -j "$JOBS"
cmake --install "$src/.build"

echo "== chromaprint build done: $PREFIX"
