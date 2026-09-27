#!/bin/sh
# build-deps.sh — fetch, verify and build the adopted external libraries
# statically into build/prefix, in the order given by scripts/build-deps.txt.
#
# This script is meant to run on the CI macOS arm64 runner (it downloads and
# compiles). It does not build FFmpeg itself (see build-ffmpeg.sh).
#
# Usage: scripts/build-deps.sh
# Env:   JOBS (default: number of CPUs), PREFIX (default: build/prefix)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
DL="${DL:-$ROOT/build/downloads}"
SRC="${SRC:-$ROOT/build/src}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
RECIPES="$HERE/build-deps.txt"
PINS="$ROOT/deps.txt"
# bump when build logic changes, to force rebuilds
RECIPE_REV=1

export CC="${CC:-clang}"
export CXX="${CXX:-clang++}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
# some libraries pass GCC-only -Wno-* flags together with -Werror; keep clang
# from failing on the unknown options (generic across all libraries)
export CFLAGS="-Wno-unknown-warning-option ${CFLAGS:-}"
export CXXFLAGS="-Wno-unknown-warning-option ${CXXFLAGS:-}"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"
export CMAKE_PREFIX_PATH="$PREFIX"
export CPPFLAGS="-I$PREFIX/include ${CPPFLAGS:-}"
export LDFLAGS="-L$PREFIX/lib ${LDFLAGS:-}"

mkdir -p "$DL" "$SRC" "$PREFIX"
LOGS="${LOGS:-$ROOT/build/logs}"; mkdir -p "$LOGS"

. "$HERE/lib.sh"

build_generic() {
  name="$1"; kind="$2"; flags="$3"
  flags=$(printf '%s' "$flags" | sed "s|@PREFIX@|$PREFIX|g")
  src="$SRC/$name"
  case "$kind" in
    autotools)
      ( cd "$src"
        # git archives often have no generated configure; bootstrap if needed.
        [ -x ./configure ] || { [ -x ./autogen.sh ] && NOCONFIGURE=1 ./autogen.sh || autoreconf -fi; }
        # modern macOS ld rejects the obsolete -force_cpusubtype_ALL that some
        # configure scripts inject for darwin; strip it generically.
        if [ -f ./configure ]; then perl -pi -e 's/ -force_cpusubtype_ALL//g' ./configure; fi
        ./configure --prefix="$PREFIX" --enable-static --disable-shared $flags
        make -j"$JOBS" && make install ) ;;
    cmake)
      cmake -S "$src" -B "$src/.build" \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" $flags
      cmake --build "$src/.build" -j "$JOBS"
      cmake --install "$src/.build" ;;
    meson)
      meson setup "$src/.build" "$src" --prefix="$PREFIX" \
        --default-library=static --buildtype=release $flags
      ninja -C "$src/.build"
      ninja -C "$src/.build" install ;;
    perl)
      ( cd "$src" && ./Configure --prefix="$PREFIX" --openssldir="$PREFIX/ssl" $flags \
        && make -j"$JOBS" && make install_sw ) ;;
    *)
      echo "ERROR: unknown kind '$kind' for $name" >&2; exit 1 ;;
  esac
}

# --- special cases -----------------------------------------------------------

build_libaom() {
  src="$SRC/libaom"
  cmake -S "$src" -B "$src/.build" \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DAOM_TARGET_CPU=arm64 -DCONFIG_RUNTIME_CPU_DETECT=0 \
    -DENABLE_TESTS=0 -DENABLE_EXAMPLES=0 -DENABLE_TOOLS=0 \
    -DCONFIG_AV1_ENCODER=1 -DCONFIG_AV1_DECODER=1
  cmake --build "$src/.build" -j "$JOBS"
  cmake --install "$src/.build"
}

build_libvpx() {
  ( cd "$SRC/libvpx" && ./configure --prefix="$PREFIX" --target=arm64-darwin-gcc \
      --enable-static --disable-shared --disable-examples --disable-tools \
      --disable-docs --disable-unit-tests --enable-vp8 --enable-vp9 \
      --enable-vp9-highbitdepth --enable-postproc --enable-multi-res-encoding --enable-pic \
    && make -j"$JOBS" && make install )
}

build_librav1e() {
  ( cd "$SRC/librav1e" && cargo cinstall --release --prefix "$PREFIX" \
      --library-type staticlib --locked )
}

build_openh264() {
  ( cd "$SRC/openh264" && make -j"$JOBS" && make install PREFIX="$PREFIX" )
}

build_libgsm() {
  ( cd "$SRC/libgsm" && make -j"$JOBS" && make install PREFIX="$PREFIX" )
}

build_quirc() {
  ( cd "$SRC/quirc" && make -j"$JOBS" libquirc.a )
  mkdir -p "$PREFIX/lib" "$PREFIX/include"
  cp "$SRC/quirc/libquirc.a" "$PREFIX/lib/"
  cp "$SRC/quirc/lib/quirc.h" "$PREFIX/include/"
}

build_libflite() {
  ( cd "$SRC/libflite" && ./configure --prefix="$PREFIX" && make -j"$JOBS" && make install )
}

build_x265() {
  cmake -S "$SRC/x265/source" -B "$SRC/x265/build-cmake" \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DENABLE_CLI=OFF -DENABLE_SHARED=OFF \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET"
  cmake --build "$SRC/x265/build-cmake" -j "$JOBS"
  cmake --install "$SRC/x265/build-cmake"
}

build_libvmaf() {
  meson setup "$SRC/libvmaf/.build" "$SRC/libvmaf/libvmaf" --prefix="$PREFIX" \
    --default-library=static --buildtype=release \
    -Denable_tests=false -Denable_docs=false -Denable_tools=false
  ninja -C "$SRC/libvmaf/.build"
  ninja -C "$SRC/libvmaf/.build" install
}

dispatch_build() {
  name="$1"; kind="$2"; flags="$3"
  case "$name" in
    libaom)      build_libaom ;;
    libvpx)      build_libvpx ;;
    librav1e)    build_librav1e ;;
    openh264)    build_openh264 ;;
    libgsm)      build_libgsm ;;
    quirc)       build_quirc ;;
    libflite)    build_libflite ;;
    x265)        build_x265 ;;
    libvmaf)     build_libvmaf ;;
    *)           build_generic "$name" "$kind" "$flags" ;;
  esac
}

# per-library marker keyed by recipe revision, build kind, flags and version;
# changing any of them forces a rebuild of just that library
marker_for() { # name kind flags
  name="$1"; kind="$2"; flags="$3"
  ver=$(awk -F'|' -v n="$name" '$1 == n { print $2; exit }' "$PINS")
  printf '%s/.built-%s-%s' "$PREFIX" "$name" \
    "$(printf '%s|%s|%s|%s' "$RECIPE_REV" "$kind" "$flags" "$ver" | shasum -a 256 | cut -c1-12)"
}

build_one() {
  name="$1"; kind="$2"; flags="$3"; marker="$4"
  echo "== build $name ($kind)"
  log="$LOGS/$name.log"
  if ! ( set -e; dispatch_build "$name" "$kind" "$flags" ) > "$log" 2>&1; then
    echo "FAILED: $name (log: build/logs/$name.log)"
    tail -n 40 "$log" 2>/dev/null || true
    exit 1
  fi
  touch "$marker"
}

# --- main --------------------------------------------------------------------

# --- prefix-local shims needed by FFmpeg's configure ---
mkdir -p "$PREFIX/lib/pkgconfig" "$PREFIX/lib"
sdk=$(xcrun --show-sdk-path)
# macOS ships libxml2 in the SDK but no pkg-config file; provide one (Apple dylib).
cat > "$PREFIX/lib/pkgconfig/libxml-2.0.pc" <<EOF
prefix=$sdk/usr
exec_prefix=\${prefix}
libdir=\${prefix}/lib
includedir=$sdk/usr/include/libxml2

Name: libXML
Version: 2.9.13
Libs: -L\${libdir} -lxml2
Cflags: -I\${includedir}
EOF
# FFmpeg's libsnappy check links -lstdc++, which macOS lacks; alias it to libc++.
rm -f "$PREFIX/lib/libstdc++.tbd"; ln -s "$sdk/usr/lib/libc++.tbd" "$PREFIX/lib/libstdc++.tbd"

grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$RECIPES" | while IFS='|' read -r name kind flags note; do
  name=$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  kind=$(printf '%s' "$kind" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  flags=$(printf '%s' "$flags" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [ -n "$name" ] || continue
  marker=$(marker_for "$name" "$kind" "$flags")
  if [ -f "$marker" ]; then
    echo "== skip $name (already built)"
    continue
  fi
  fetch "$name"
  build_one "$name" "$kind" "$flags" "$marker"
done

echo "== deps build done: $PREFIX"
