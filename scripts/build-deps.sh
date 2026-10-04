#!/bin/sh
# build-deps.sh — fetch, verify and build the adopted external libraries
# statically into build/prefix, in the order given by scripts/data/build-deps.txt.
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
# space-separated library names to build (default: every recipe)
ONLY="${ONLY:-}"
RECIPES="$HERE/data/build-deps.txt"
PINS="$ROOT/deps.txt"
PATCHES="$HERE/data/patches.txt"
# this build always links statically, so `linkage=static` patches always apply
LINKAGE=static
# bump when build logic changes, to force rebuilds (the per-library marker uses
# the data flags, so a flag that lives in code needs this to take effect)
RECIPE_REV=10

. "$HERE/lib.sh"
export CC="${CC:-clang}"
export CXX="${CXX:-clang++}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
export CFLAGS="${CFLAGS:-}"
# Newer libc++ no longer provides size_t transitively, so force it into every
# translation unit. The preinclude is language-aware because some libraries
# compile C sources through the C++ driver (opencore-amr sets "-x c" in
# AM_CXXFLAGS), where a C++ header like <cstddef> cannot be found.
preinc="$ROOT/build/preinclude"; mkdir -p "$preinc"
cat > "$preinc/size_t.h" <<'EOF'
#ifdef __cplusplus
#include <cstddef>
#else
#include <stddef.h>
#endif
EOF
export CXXFLAGS="-include $preinc/size_t.h ${CXXFLAGS:-}"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"
# close the pkg-config search to the prefix so Homebrew/system .pc files are
# never picked up (any missing dependency must be provided in the prefix)
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"
export CMAKE_PREFIX_PATH="$PREFIX"
export CPPFLAGS="-I$PREFIX/include ${CPPFLAGS:-}"
export LDFLAGS="-L$PREFIX/lib ${LDFLAGS:-}"

mkdir -p "$DL" "$SRC" "$PREFIX"
LOGS="${LOGS:-$ROOT/build/logs}"; mkdir -p "$LOGS"
# per-library record of the files its install created, so a rebuild can remove
# the previous ones first (a cached prefix may hold files from an older install)
MANIFEST="$PREFIX/.manifest"

# Apply the source patches listed for a library in scripts/data/patches.txt. Each is
# checked with `git apply --check` first: a patch that no longer applies means
# upstream changed the code, so the build fails loudly instead of silently
# skipping the patch (see docs/PATCH-POLICY.md).
apply_patches() { # <name>
  lib_want="$1"
  [ -f "$PATCHES" ] || return 0
  while IFS='|' read -r lib vdir pfile cls link || [ -n "$lib" ]; do
    case "$lib" in ''|'#'*) continue ;; esac
    [ "$lib" = "$lib_want" ] || continue
    if [ "$link" = static ] && [ "$LINKAGE" != static ]; then
      echo "== patch $lib: skipped ($pfile is static-only)"
      continue
    fi
    pf="$ROOT/patches/$lib/$vdir/$pfile"
    if [ ! -f "$pf" ]; then
      echo "ERROR: patch not found: $pf" >&2; exit 1
    fi
    if ! ( cd "$SRC/$lib" && git apply --check -p1 "$pf" ); then
      echo "ERROR: patch does not apply: $lib/$vdir/$pfile" >&2
      echo "       (upstream changed the code -> refresh or remove the patch)" >&2
      exit 1
    fi
    ( cd "$SRC/$lib" && git apply -p1 "$pf" )
    echo "== patch $lib: $pfile ($cls) applied"
  done < "$PATCHES"
}

build_generic() {
  name="$1"; kind="$2"; flags="$3"; subdir="$4"
  flags=$(printf '%s' "$flags" | sed "s|@PREFIX@|$PREFIX|g")
  src="$SRC/$name"
  case "$kind" in
    autotools)
      ( cd "$src/$subdir"
        # git archives often have no generated configure; bootstrap if needed.
        [ -x ./configure ] || { echo "== patch: bootstrapping (no generated configure)"; [ -x ./autogen.sh ] && NOCONFIGURE=1 ./autogen.sh || autoreconf -fi; }
        # the runner's newer autotools would regenerate pre-generated files
        # during make (autoheader drops the legacy VERSION define in config.h.in);
        # keep the generated files newer than their autotools inputs.
        touch aclocal.m4 configure config.h.in Makefile.in 2>/dev/null || true
        # modern macOS ld rejects the obsolete -force_cpusubtype_ALL that some
        # configure scripts inject for darwin; strip it generically.
        if [ -f ./configure ]; then
          n=$(grep -coE ' -force_cpusubtype_ALL' ./configure || true)
          if [ "$n" -gt 0 ]; then echo "== patch: -force_cpusubtype_ALL strip: $n occurrence(s)"; fi
          perl -pi -e 's/ -force_cpusubtype_ALL//g' ./configure
        fi
        ./configure --prefix="$PREFIX" --enable-static --disable-shared $flags
        # some configure scripts promote GCC-targeted warnings to errors
        # (-Werror), which newer clang trips on; strip it from the generated
        # makefiles (after configure, so feature detection is unaffected).
        n=$(find . -name Makefile -exec grep -hoE ' ?-Werror(=[A-Za-z0-9_-]+)?' {} + 2>/dev/null | wc -l | tr -d ' ')
        if [ "$n" -gt 0 ]; then echo "== patch: -Werror strip: $n occurrence(s)"; fi
        find . -name Makefile -exec perl -pi -e 's/ ?-Werror(?:=[A-Za-z0-9_-]+)?//g' {} + 2>/dev/null || true
        make -j"$JOBS" && make install ) ;;
    cmake)
      cmake -S "$src/$subdir" -B "$src/.build" \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" $flags
      cmake --build "$src/.build" -j "$JOBS"
      cmake --install "$src/.build" ;;
    meson)
      meson setup "$src/.build" "$src/$subdir" --prefix="$PREFIX" \
        --default-library=static --buildtype=release $flags
      ninja -C "$src/.build"
      ninja -C "$src/.build" install ;;
    perl)
      ( cd "$src/$subdir" && ./Configure --prefix="$PREFIX" --openssldir="$PREFIX/ssl" $flags \
        && make -j"$JOBS" && make install_sw ) ;;
    *)
      echo "ERROR: unknown kind '$kind' for $name" >&2; exit 1 ;;
  esac
}

# --- special cases -----------------------------------------------------------

build_librav1e() { # <flags from the recipe>
  ( cd "$SRC/librav1e" && cargo cinstall --prefix "$PREFIX" $1 )
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

dispatch_build() {
  name="$1"; kind="$2"; flags="$3"; subdir="$4"

  # source patches come from data (scripts/data/patches.txt) and are applied with a
  # precondition check; see apply_patches() and docs/PATCH-POLICY.md
  apply_patches "$name"

  # Builders for libraries without a supported generic build/install path.
  case "$name" in
    librav1e)    build_librav1e "$flags" ;;
    libgsm)      build_libgsm ;;
    quirc)       build_quirc ;;
    *)           build_generic "$name" "$kind" "$flags" "$subdir" ;;
  esac
}

# per-library marker keyed by recipe revision, build kind, flags and version;
# changing any of them forces a rebuild of just that library
marker_for() { # name kind flags subdir
  name="$1"; kind="$2"; flags="$3"; subdir="$4"
  ver=$(pin_version "$name")
  printf '%s/.built-%s-%s' "$PREFIX" "$name" \
    "$(printf '%s|%s|%s|%s|%s' "$RECIPE_REV" "$kind" "$flags" "$subdir" "$ver" | shasum -a 256 | cut -c1-12)"
}

# true when the named library is listed in FORCE_REBUILD
is_forced() {
  case " $FORCE_REBUILD " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# true when ONLY is empty or lists the named library
is_selected() {
  [ -z "$ONLY" ] && return 0
  case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# every file in the prefix (relative), excluding the manifest bookkeeping.
# Directories are not recorded: an install recreates the ones it needs, and a
# lingering empty one is harmless, while rm -f cannot remove it.
prefix_files() {
  ( cd "$PREFIX" && find . -path './.manifest' -prune -o \( -type f -o -type l \) -print | sort )
}

build_one() {
  name="$1"; kind="$2"; flags="$3"; marker="$4"; subdir="$5"
  echo "== build $name ($kind)"
  log="$LOGS/$name.log"

  # Remove what this library installed last time before rebuilding it: a cached
  # prefix can keep files from an older install (e.g. a stale static archive),
  # which would silently mask the rebuild.
  mf="$MANIFEST/$name"
  if [ -f "$mf" ]; then
    removed=0
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      rm -f "$PREFIX/$f"
      removed=$((removed + 1))
    done < "$mf"
    echo "== manifest $name: removed $removed previous file(s)"
  fi

  before=$(mktemp); after=$(mktemp)
  prefix_files > "$before"

  # Run the recipe with errexit active. Testing the subshell directly (if/&&/||)
  # would disable errexit inside it, letting a late success mask an earlier
  # failure, so capture the status separately.
  set +e
  ( set -e; dispatch_build "$name" "$kind" "$flags" "$subdir" ) > "$log" 2>&1
  st=$?
  set -e
  if [ "$st" -ne 0 ]; then
    echo "FAILED: $name (log: build/logs/$name.log)"
    tail -n 40 "$log" 2>/dev/null || true
    rm -f "$before" "$after"
    exit 1
  fi

  # record what this install added, for the next rebuild
  prefix_files > "$after"
  mkdir -p "$MANIFEST"
  comm -13 "$before" "$after" > "$mf"
  echo "== manifest $name: recorded $(wc -l < "$mf" | tr -d ' ') file(s)"
  rm -f "$before" "$after"

  touch "$marker"
}

# --- main --------------------------------------------------------------------

# --- prefix-local shims needed by FFmpeg's configure ---
mkdir -p "$PREFIX/lib/pkgconfig" "$PREFIX/lib"
sdk=$(xcrun --show-sdk-path)
# macOS ships libxml2 in the SDK but no pkg-config file; provide one (Apple dylib).
# NOTE: pkgconf skips a .pc file that does not declare Name/Description/Version
# (it reports "not found in the pkg-config search path"), so keep all three.
cat > "$PREFIX/lib/pkgconfig/libxml-2.0.pc" <<EOF
prefix=$sdk/usr
exec_prefix=\${prefix}
libdir=\${prefix}/lib
includedir=$sdk/usr/include/libxml2

Name: libXML
Description: libXML XML parser (macOS SDK)
Version: 2.9.13
Libs: -L\${libdir} -lxml2
Cflags: -I\${includedir}
EOF
# zlib lives in the SDK (Apple) but has no pkg-config file; provide a shim so
# the closed search still resolves it to the system libz.
cat > "$PREFIX/lib/pkgconfig/zlib.pc" <<EOF
prefix=$sdk/usr
exec_prefix=\${prefix}
libdir=\${prefix}/lib
includedir=\${prefix}/include

Name: zlib
Description: zlib compression library (macOS SDK)
Version: 1.2.12
Libs: -L\${libdir} -lz
Cflags: -I\${includedir}
EOF
# FFmpeg's libsnappy check links -lstdc++, which macOS lacks; alias it to libc++.
rm -f "$PREFIX/lib/libstdc++.tbd"; ln -s "$sdk/usr/lib/libc++.tbd" "$PREFIX/lib/libstdc++.tbd"

grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$RECIPES" | while IFS='|' read -r name kind flags note subdir; do
  name=$(trim "$name")
  kind=$(trim "$kind")
  flags=$(trim "$flags")
  subdir=$(trim "$subdir")
  [ -n "$name" ] || continue
  is_selected "$name" || continue
  marker=$(marker_for "$name" "$kind" "$flags" "$subdir")
  if [ -f "$marker" ] && ! is_forced "$name"; then
    echo "== skip $name (already built)"
    continue
  fi
  fetch "$name"
  build_one "$name" "$kind" "$flags" "$marker" "$subdir"
done

# Some libraries install their static archive into a subdirectory while their
# pkg-config file only adds $PREFIX/lib; flatten so -l<name> resolves it. Always
# refresh the copy: a cached prefix can hold a stale one from an earlier build,
# which would silently mask a rebuilt library.
flattened=0
for a in "$PREFIX"/lib/*/lib*.a; do
  [ -e "$a" ] || continue
  base=$(basename "$a")
  cp -f "$a" "$PREFIX/lib/$base"
  flattened=$((flattened + 1))
done
echo "== flattened static archives: $flattened"

# The static ffmpeg must link only Apple dylibs, and the linker prefers a
# .dylib over the .a, so drop any third-party shared library from the prefix
# (including stale ones left by a cached prefix).
stale=$(find "$PREFIX/lib" -name '*.dylib' 2>/dev/null || true)
if [ -n "$stale" ]; then
  echo "== removing third-party shared libraries from the prefix"
  printf '%s\n' "$stale"
  printf '%s\n' "$stale" | while IFS= read -r f; do rm -f "$f"; done
else
  echo "== no third-party shared libraries in the prefix"
fi

# Pre-fetch the FFmpeg tarball so the downloads cache (saved after this step)
# carries it; build-ffmpeg.sh also ensures it when run standalone.
[ -n "${SOURCE_PACKAGE:-}" ] || download ffmpeg

echo "== deps build done: $PREFIX"
