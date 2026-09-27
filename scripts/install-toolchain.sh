#!/bin/sh
# install-toolchain.sh — install the pinned build tools from their official
# distributions (not brew) and add them to PATH.
#
# The versions come from the environment (set by the workflow). The script is
# idempotent: anything already present (for example restored from the CI
# cache) is left untouched. Tools that cannot be pinned (the runner image,
# Xcode/SDK, system tools) are recorded by record-toolchain.sh instead.
set -eu

: "${CMAKE_VERSION:?}" "${NINJA_VERSION:?}" "${MESON_VERSION:?}"
: "${RUST_VERSION:?}" "${CARGO_C_VERSION:?}"

TOOLS="${TOOLS:-$HOME/tools}"
CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"

add_path() { [ -n "${GITHUB_PATH:-}" ] && printf '%s\n' "$1" >> "$GITHUB_PATH"; }

# --- CMake (official macOS tarball) ---
cmake_bin="$TOOLS/cmake-$CMAKE_VERSION-macos-universal/CMake.app/Contents/bin"
if [ ! -x "$cmake_bin/cmake" ]; then
  mkdir -p "$TOOLS"
  curl -sSL --fail -o /tmp/cmake.tar.gz \
    "https://github.com/Kitware/CMake/releases/download/v$CMAKE_VERSION/cmake-$CMAKE_VERSION-macos-universal.tar.gz"
  tar -xzf /tmp/cmake.tar.gz -C "$TOOLS"
fi
add_path "$cmake_bin"

# --- Ninja (official macOS binary) ---
if [ ! -x "$TOOLS/ninja/ninja" ]; then
  mkdir -p "$TOOLS/ninja"
  curl -sSL --fail -o /tmp/ninja.zip \
    "https://github.com/ninja-build/ninja/releases/download/v$NINJA_VERSION/ninja-mac.zip"
  unzip -o /tmp/ninja.zip -d "$TOOLS/ninja"
fi
add_path "$TOOLS/ninja"

# --- Meson (venv: the runner's python3 is Homebrew-managed, PEP 668) ---
if [ ! -x "$TOOLS/meson-venv/bin/meson" ]; then
  python3 -m venv "$TOOLS/meson-venv"
  "$TOOLS/meson-venv/bin/pip" install --quiet "meson==$MESON_VERSION"
fi
add_path "$TOOLS/meson-venv/bin"

# --- Rust (rustup) + cargo-c ---
if [ ! -x "$CARGO_HOME/bin/rustc" ]; then
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain "$RUST_VERSION"
fi
PATH="$CARGO_HOME/bin:$PATH"
export PATH
if [ ! -x "$CARGO_HOME/bin/cargo-cinstall" ]; then
  cargo install cargo-c --version "$CARGO_C_VERSION" --locked
fi
add_path "$CARGO_HOME/bin"

# --- verify the pinned versions are the ones we just installed ---
fail=0
check() { # tool expected-version-args...
  name="$1"; shift
  got=$("$@" 2>&1 | head -n1)
  case "$got" in
    *"$EXPECT"*) printf 'ok   %s: %s\n' "$name" "$got" ;;
    *) printf 'BAD  %s: %s (expected %s)\n' "$name" "$got" "$EXPECT"; fail=1 ;;
  esac
}
EXPECT=$CMAKE_VERSION check cmake "$cmake_bin/cmake" --version
EXPECT=$NINJA_VERSION check ninja "$TOOLS/ninja/ninja" --version
EXPECT=$MESON_VERSION check meson "$TOOLS/meson-venv/bin/meson" --version
EXPECT=$RUST_VERSION check rustc "$CARGO_HOME/bin/rustc" --version
EXPECT=$CARGO_C_VERSION check cargo-c "$CARGO_HOME/bin/cargo-cinstall" --version
[ "$fail" -eq 0 ] || { echo "ERROR: pinned toolchain mismatch" >&2; exit 1; }
echo "== pinned toolchain installed (CMake $CMAKE_VERSION, Ninja $NINJA_VERSION, Meson $MESON_VERSION, Rust $RUST_VERSION, cargo-c $CARGO_C_VERSION)"
