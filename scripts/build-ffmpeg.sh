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
# search only the prefix (no Homebrew/system .pc files)
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"

# --- extract pinned FFmpeg source ---
# download and verify the tarball on demand (verify-pins.py pre-fetches it when
# pin verification is enabled, but it must not be a prerequisite)
PINS="$ROOT/deps.txt"
. "$HERE/lib.sh"
if [ ! -d "$SRC/ffmpeg" ]; then
  fetch ffmpeg
fi

# Pass 1 builds in its own tree so both passes can be cached and restored
# independently (the full pass keeps the canonical build/src/ffmpeg tree).
if [ "$CHROMAPRINT" -eq 1 ]; then
  builddir="$SRC/ffmpeg"
else
  builddir="$SRC/ffmpeg-pass1"
  [ -d "$builddir" ] || cp -R "$SRC/ffmpeg" "$builddir"
fi

# --- configure ---
# NOTE: --disable-vulkan because Vulkan on macOS needs MoltenVK (a third-party
# dylib), which the Apple-only runtime rule forbids.
# NOTE: the extra libs below are private dependencies that some of the static
# libraries' pkg-config files omit (e.g. libjxl_threads.pc lacks -lc++, and
# libssh.pc lacks -lssl -lcrypto -lz; chromaprint.pc lacks the Accelerate
# framework it uses for vDSP), so they are supplied explicitly.
extra_libs="-lpthread -lm -lc++ -lssl -lcrypto -lz -framework Accelerate"
set -- \
  --prefix="$PREFIX" \
  --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX/include" \
  --extra-ldflags="-L$PREFIX/lib" \
  --extra-libs="$extra_libs" \
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
  --enable-libdvdread --enable-libdvdnav --disable-libzvbi \
  --enable-libssh --enable-libsrt --enable-librist --enable-libzmq --enable-librabbitmq \
  --enable-libqrencode --enable-libquirc --enable-libtesseract --enable-libcaca --enable-libflite \
  --enable-videotoolbox --enable-audiotoolbox --enable-avfoundation \
  --enable-coreimage --enable-opencl --enable-metal \
  --disable-vulkan \
  --disable-xlib --disable-libxcb

if [ "$CHROMAPRINT" -eq 1 ]; then
  set -- "$@" --enable-chromaprint
fi

LOGS="$ROOT/build/logs"; mkdir -p "$LOGS"
if [ "$CHROMAPRINT" -eq 1 ]; then PASS=full; else PASS=pass1; fi

# Reuse an existing configuration when the tree (possibly restored from the CI
# cache) was configured with identical arguments, so make runs incrementally.
stamp="$builddir/ffbuild/.configure-stamp"
if [ -f "$stamp" ] && [ -f "$builddir/ffbuild/config.mak" ] \
   && [ "$(cat "$stamp")" = "$(printf '%s\n' "$@")" ]; then
  echo "== reuse existing FFmpeg configuration ($PASS)"
else
  if ! ( set -e; cd "$builddir" && ./configure "$@" ) > "$LOGS/ffmpeg-configure-$PASS.log" 2>&1; then
    echo "FAILED: ffmpeg configure ($PASS) (log: build/logs/ffmpeg-configure-$PASS.log; see also $builddir/ffbuild/config.log)"
    tail -n 40 "$LOGS/ffmpeg-configure-$PASS.log" 2>/dev/null || true
    exit 1
  fi
  printf '%s\n' "$@" > "$stamp"
fi

# --- build ---
# make does not track external libraries as prerequisites, so force a relink
# against the (possibly rebuilt) prefix libraries
rm -f "$builddir/ffmpeg" "$builddir/ffprobe" "$builddir/ffplay" \
      "$builddir/ffmpeg_g" "$builddir/ffprobe_g" "$builddir/ffplay_g"
if ! ( set -e; cd "$builddir" && make -j"$JOBS" && make install ) > "$LOGS/ffmpeg-build-$PASS.log" 2>&1; then
  echo "FAILED: ffmpeg build ($PASS) (log: build/logs/ffmpeg-build-$PASS.log)"
  tail -n 40 "$LOGS/ffmpeg-build-$PASS.log" 2>/dev/null || true
  exit 1
fi

echo "== ffmpeg build done (chromaprint=$CHROMAPRINT): $builddir/ffmpeg"
