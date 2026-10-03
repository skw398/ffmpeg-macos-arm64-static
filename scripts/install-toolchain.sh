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
: "${UV_VERSION:?}" "${UV_SHA256:?}" "${PYTHON_VERSION:?}"

TOOLS="${TOOLS:-$HOME/tools}"
CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"

add_path() { [ -n "${GITHUB_PATH:-}" ] && printf '%s\n' "$1" >> "$GITHUB_PATH"; }
add_env()  { [ -n "${GITHUB_ENV:-}" ]  && printf '%s=%s\n' "$1" "$2" >> "$GITHUB_ENV"; }

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

# --- uv (official macOS tarball) ---
# Pins the Python interpreter (.python-version) and runs the Python-based
# checks, so they do not drift with the runner's Homebrew-managed python3.
uv_dir="$TOOLS/uv-$UV_VERSION"
if [ ! -x "$uv_dir/uv" ]; then
  mkdir -p "$uv_dir"
  curl -sSL --fail -o /tmp/uv.tar.gz \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz"
  echo "$UV_SHA256  /tmp/uv.tar.gz" | shasum -a 256 -c -
  tar -xzf /tmp/uv.tar.gz -C "$uv_dir" --strip-components=1
fi
add_path "$uv_dir"
# keep uv's managed Python inside the cached ~/tools
UV_PYTHON_INSTALL_DIR="$TOOLS/uv/python"
export UV_PYTHON_INSTALL_DIR
add_env UV_PYTHON_INSTALL_DIR "$UV_PYTHON_INSTALL_DIR"
"$uv_dir/uv" python install "$PYTHON_VERSION"

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
EXPECT=$UV_VERSION check uv "$uv_dir/uv" --version
EXPECT=$PYTHON_VERSION check python "$uv_dir/uv" run --no-project python --version
[ "$fail" -eq 0 ] || { echo "ERROR: pinned toolchain mismatch" >&2; exit 1; }
echo "== pinned toolchain installed (CMake $CMAKE_VERSION, Ninja $NINJA_VERSION, Meson $MESON_VERSION, Rust $RUST_VERSION, cargo-c $CARGO_C_VERSION, uv $UV_VERSION, Python $PYTHON_VERSION)"
