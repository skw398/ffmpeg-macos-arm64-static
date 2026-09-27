#!/bin/sh
# build-ffmpeg.sh — configure and build FFmpeg (static) against build/prefix.
#
# Usage:
#   scripts/build-ffmpeg.sh                 # full build (includes chromaprint)
#   scripts/build-ffmpeg.sh --no-chromaprint  # pass 1: build libav* for chromaprint
#
# The chromaprint filter links FFmpeg's own libraries, so it needs two passes:
#   1) build-deps.sh (without chromaprint) -> build-ffmpeg.sh --no-chromaprint
#   2) build chromaprint against pass-1 libav*
#   3) build-ffmpeg.sh (full)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
DL="${DL:-$ROOT/build/downloads}"
SRC="${SRC:-$ROOT/build/src}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

CHROMAPRINT=1
[ "${1:-}" = "--no-chromaprint" ] && CHROMAPRINT=0

export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"

# --- extract pinned FFmpeg source ---
# download and verify the tarball on demand (verify-pins.sh pre-fetches it when
# pin verification is enabled, but it must not be a prerequisite)
PINS="$ROOT/deps.txt"
. "$HERE/lib.sh"
if [ ! -d "$SRC/ffmpeg" ]; then
  fetch ffmpeg
fi

# --- configure ---
# NOTE: --disable-vulkan because Vulkan on macOS needs MoltenVK (a third-party
# dylib), which the Apple-only runtime rule forbids.
# NOTE: the extra libs below are private dependencies that some of the static
# libraries' pkg-config files omit (e.g. libjxl_threads.pc lacks -lc++, and
# libssh.pc lacks -lssl -lcrypto -lz), so they are supplied explicitly.
set -- \
  --prefix="$PREFIX" \
  --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX/include" \
  --extra-ldflags="-L$PREFIX/lib" \
  --extra-libs="-lpthread -lm -lc++ -lssl -lcrypto -lz" \
  --enable-gpl --enable-version3 \
  --enable-static --disable-shared --disable-debug \
  --enable-libxml2 --enable-openssl --enable-lzma \
  --enable-libdav1d --enable-libaom --enable-libsvtav1 --enable-librav1e --enable-libvpx \
  --enable-libx264 --enable-libx265 --enable-libopenh264 --enable-libvvenc \
  --enable-liboapv --enable-libxeve --enable-libxevd \
  --enable-libvidstab --enable-libsnappy \
  --enable-libopus --enable-libspeex --enable-libmp3lame --enable-libtwolame --enable-libgsm \
  --enable-libopencore-amrnb --enable-libopencore-amrwb --enable-libvo-amrwbenc \
  --enable-libcodec2 --enable-libilbc --enable-liblc3 --enable-libgme --enable-libopenmpt \
  --enable-librubberband --enable-libbs2b --enable-libsoxr --enable-libmysofa \
  --enable-libtheora --enable-libvorbis \
  --enable-libwebp --enable-libopenjpeg --enable-libzimg --enable-libjxl --enable-libvmaf \
  --enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz \
  --enable-libaribb24 --enable-libaribcaption --enable-libbluray \
  --enable-libdvdread --enable-libdvdnav --enable-libzvbi \
  --enable-libssh --enable-libsrt --enable-librist --enable-libzmq --enable-librabbitmq \
  --enable-libqrencode --enable-libquirc --enable-libtesseract --enable-libcaca --enable-libflite \
  --enable-videotoolbox --enable-audiotoolbox --enable-avfoundation \
  --enable-coreimage --enable-opencl --enable-metal \
  --disable-vulkan

if [ "$CHROMAPRINT" -eq 1 ]; then
  set -- "$@" --enable-chromaprint
fi

LOGS="$ROOT/build/logs"; mkdir -p "$LOGS"
if [ "$CHROMAPRINT" -eq 1 ]; then PASS=full; else PASS=pass1; fi
if ! ( set -e; cd "$SRC/ffmpeg" && ./configure "$@" ) > "$LOGS/ffmpeg-configure-$PASS.log" 2>&1; then
  echo "FAILED: ffmpeg configure ($PASS) (log: build/logs/ffmpeg-configure-$PASS.log; see also $SRC/ffmpeg/ffbuild/config.log)"
  tail -n 40 "$LOGS/ffmpeg-configure-$PASS.log" 2>/dev/null || true
  exit 1
fi

# --- build ---
if ! ( set -e; cd "$SRC/ffmpeg" && make -j"$JOBS" && make install ) > "$LOGS/ffmpeg-build-$PASS.log" 2>&1; then
  echo "FAILED: ffmpeg build ($PASS) (log: build/logs/ffmpeg-build-$PASS.log)"
  tail -n 40 "$LOGS/ffmpeg-build-$PASS.log" 2>/dev/null || true
  exit 1
fi

echo "== ffmpeg build done (chromaprint=$CHROMAPRINT): $SRC/ffmpeg/ffmpeg"
