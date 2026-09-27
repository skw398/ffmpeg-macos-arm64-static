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
# space-separated library names to rebuild even if their marker exists
FORCE_REBUILD="${FORCE_REBUILD:-}"
RECIPES="$HERE/build-deps.txt"
PINS="$ROOT/deps.txt"
# bump when build logic changes, to force rebuilds
RECIPE_REV=2

export CC="${CC:-clang}"
export CXX="${CXX:-clang++}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
# some libraries pass GCC-only -Wno-* flags; keep clang from failing on the
# unknown options (the -Werror promotion is removed separately in lib.sh)
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
        # the runner's newer autotools would regenerate pre-generated files
        # during make (autoheader drops the legacy VERSION define in config.h.in);
        # keep the generated files newer than their autotools inputs.
        touch aclocal.m4 configure config.h.in Makefile.in 2>/dev/null || true
        # modern macOS ld rejects the obsolete -force_cpusubtype_ALL that some
        # configure scripts inject for darwin; strip it generically.
        if [ -f ./configure ]; then perl -pi -e 's/ -force_cpusubtype_ALL//g' ./configure; fi
        ./configure --prefix="$PREFIX" --enable-static --disable-shared $flags
        # some configure scripts promote GCC-targeted warnings to errors
        # (-Werror), which newer clang trips on; strip it from the generated
        # makefiles (after configure, so feature detection is unaffected).
        find . -name Makefile -exec perl -pi -e 's/ ?-Werror(?:=[A-Za-z0-9_-]+)?//g' {} + 2>/dev/null || true
        make -j"$JOBS" && make install ) ;;
    cmake)
      cmake -S "$src" -B "$src/.build" \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" $flags
      cmake --build "$src/.build" -j "$JOBS"
      cmake --install "$src/.build" ;;
    meson)
      meson setup "$src/.build" "$src" --prefix="$PREFIX" \
        --default-library=static --buildtype=release -Dwerror=false $flags
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
    -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DAOM_TARGET_CPU=arm64 -DCONFIG_RUNTIME_CPU_DETECT=0 \
    -DENABLE_TESTS=0 -DENABLE_EXAMPLES=0 -DENABLE_TOOLS=0 \
    -DCONFIG_AV1_ENCODER=1 -DCONFIG_AV1_DECODER=1
  cmake --build "$src/.build" -j "$JOBS"
  cmake --install "$src/.build"
}

build_libvpx() {
  flags="$1"
  # libvpx does not support switching the target in a tree that still holds
  # build output; the git-source cache keeps them, so clean before configuring
  ( cd "$SRC/libvpx" \
    && { [ -d .git ] && git clean -xffdq || true; } \
    && ./configure --prefix="$PREFIX" $flags \
    && { grep -E '^(CFLAGS|ASFLAGS|LDFLAGS)=' config.mk || true; } \
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
  ( cd "$SRC/libgsm" && make -j"$JOBS" )
  # libgsm's install target needs INSTALL_ROOT and uses an "inc" directory, so
  # place the library and header where FFmpeg's configure looks for them
  mkdir -p "$PREFIX/lib" "$PREFIX/include"
  cp "$SRC/libgsm/lib/libgsm.a" "$PREFIX/lib/"
  cp "$SRC/libgsm/inc/gsm.h" "$PREFIX/include/"
}

build_quirc() {
  # SDL is only needed by quirc's demos; its makefile captures pkg-config's
  # stderr into SDL_CFLAGS, whose quotes break the compile command, so blank it
  ( cd "$SRC/quirc" && make -j"$JOBS" SDL_CFLAGS= libquirc.a ) \
    && mkdir -p "$PREFIX/lib" "$PREFIX/include" \
    && cp "$SRC/quirc/libquirc.a" "$PREFIX/lib/" \
    && cp "$SRC/quirc/lib/quirc.h" "$PREFIX/include/"
}

build_libflite() {
  # libflite's install uses GNU cp -d, which macOS cp lacks
  ( cd "$SRC/libflite" && ./configure --prefix="$PREFIX" && make -j"$JOBS" \
    && perl -pi -e 's/\bcp -pd\b/cp -p/g' main/Makefile \
    && make install )
}

build_x265() {
  cmake -S "$SRC/x265/source" -B "$SRC/x265/build-cmake" \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DENABLE_CLI=OFF -DENABLE_SHARED=OFF \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
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

  # source patches (applied in build/src, not committed upstream)
  case "$name" in
    libssh)
      # CMake 4 errors in libssh's CompilerChecks flag probing; replace it with
      # the module it would have provided (check_c_compiler_flag)
      perl -pi -e 's/include\(CompilerChecks\.cmake\)/include(CheckCCompilerFlag)/' \
        "$SRC/libssh/CMakeLists.txt" ;;
    libqrencode)
      # autoconf >= 2.70 no longer defines VERSION from AC_INIT in config.h;
      # qrencode.c uses it for QRcode_APIVersionString(), so restore the define
      perl -0pi -e 's/(AC_INIT\([^\n]*\)\n)/$1AC_DEFINE_UNQUOTED([VERSION], ["\$PACKAGE_VERSION"], [Package version])\n/' \
        "$SRC/libqrencode/configure.ac" ;;
    libcaca)
      # common-image.c calls the internal _caca_alloc2d without including the
      # internal header; newer clang errors on the implicit declaration
      perl -pi -e 's/^#include "common-image.h"$/#include "common-image.h"\n#include "caca_internals.h"/' \
        "$SRC/libcaca/src/common-image.c" ;;
    libcodec2)
      # codec2 and speex both export these speex-derived helpers; rename
      # codec2's so the static link has no duplicate symbols
      find "$SRC/libcodec2" -type f \( -name '*.c' -o -name '*.h' \) \
        -exec perl -pi -e 's/\blsp_to_lpc\b/codec2_lsp_to_lpc/g; s/\blpc_to_lsp\b/codec2_lpc_to_lsp/g' {} + ;;
    libzmq)
      # libzmq and libssh both export sha1_*; rename libzmq's internal ones
      perl -pi -e 's/\bsha1_(init|pad|loop|result|step)\b/zmq_sha1_$1/g' \
        "$SRC/libzmq/external/sha1/sha1.c" "$SRC/libzmq/external/sha1/sha1.h" ;;
  esac

  case "$name" in
    libaom)      build_libaom ;;
    libvpx)      build_libvpx "$flags" ;;
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

# true when the named library is listed in FORCE_REBUILD
is_forced() {
  case " $FORCE_REBUILD " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

build_one() {
  name="$1"; kind="$2"; flags="$3"; marker="$4"
  echo "== build $name ($kind)"
  log="$LOGS/$name.log"
  # Run the recipe with errexit active. Testing the subshell directly (if/&&/||)
  # would disable errexit inside it, letting a late success mask an earlier
  # failure, so capture the status separately.
  set +e
  ( set -e; dispatch_build "$name" "$kind" "$flags" ) > "$log" 2>&1
  st=$?
  set -e
  if [ "$st" -ne 0 ]; then
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
  if [ -f "$marker" ] && ! is_forced "$name"; then
    echo "== skip $name (already built)"
    continue
  fi
  fetch "$name"
  build_one "$name" "$kind" "$flags" "$marker"
done

# Some libraries install their static archive into a subdirectory while their
# pkg-config file only adds $PREFIX/lib; flatten so -l<name> resolves it.
for a in "$PREFIX"/lib/*/lib*.a; do
  [ -e "$a" ] || continue
  base=$(basename "$a")
  [ -e "$PREFIX/lib/$base" ] || cp -f "$a" "$PREFIX/lib/$base"
done

# The static ffmpeg must link only Apple dylibs, and the linker prefers a
# .dylib over the .a, so drop any third-party shared library from the prefix
# (including stale ones left by a cached prefix).
stale=$(find "$PREFIX/lib" -name '*.dylib' 2>/dev/null || true)
if [ -n "$stale" ]; then
  echo "== removing third-party shared libraries from the prefix"
  printf '%s\n' "$stale"
  printf '%s\n' "$stale" | while IFS= read -r f; do rm -f "$f"; done
fi

# Pre-fetch the FFmpeg tarball so the downloads cache (saved after this step)
# carries it; build-ffmpeg.sh also ensures it when run standalone.
download ffmpeg

echo "== deps build done: $PREFIX"
