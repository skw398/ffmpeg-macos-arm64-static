#!/bin/sh
# record-toolchain.sh — print the versions of the runner and the build tools.
#
# Some inputs cannot be pinned on a GitHub-hosted runner (the macOS image,
# Xcode/SDK, and system tools such as perl/make/bash). This manifest records
# what was actually used so a build can be traced back to its toolchain.
set -u

line() { printf '%s\n' "$*"; }

# version string of a tool (some need special handling)
ver() {
  case "$1" in
    perl) perl -e 'printf "%vd\n", $^V' 2>/dev/null ;;
    *)    "$1" --version 2>&1 | head -n 1 ;;
  esac
}

line "== runner =="
line "uname: $(uname -a)"
line "sw_vers: $(sw_vers -productVersion 2>/dev/null || echo n/a)"
line "arch: $(uname -m)"
if command -v xcodebuild >/dev/null 2>&1; then
  line "xcodebuild: $(xcodebuild -version 2>/dev/null | tr '\n' ' ')"
fi
if command -v xcrun >/dev/null 2>&1; then
  line "sdk: $(xcrun --show-sdk-version 2>/dev/null || echo n/a) ($(xcrun --show-sdk-path 2>/dev/null || echo n/a))"
fi

line "== compilers =="
for t in clang clang++ gcc cc make; do
  if command -v "$t" >/dev/null 2>&1; then
    line "$t: $("$t" --version 2>&1 | head -n 1) [$(command -v "$t")]"
  else
    line "$t: (not found)"
  fi
done

line "== build tools =="
for t in cmake ninja meson nasm pkg-config pkgconf autoconf automake m4 \
         libtool glibtool glibtoolize gettext perl python3 rustc cargo cargo-c \
         git curl tar; do
  if command -v "$t" >/dev/null 2>&1; then
    line "$t: $(ver "$t") [$(command -v "$t")]"
  else
    line "$t: (not found)"
  fi
done

line "== homebrew =="
if command -v brew >/dev/null 2>&1; then
  line "brew: $(brew --version 2>/dev/null | head -n 1) [$(command -v brew)]"
  line "brew prefix: $(brew --prefix 2>/dev/null || echo n/a)"
  line "-- brew list --versions (build tools) --"
  brew list --versions cmake ninja meson nasm pkgconf autoconf automake \
    libtool gettext perl rust cargo-c 2>/dev/null || true
else
  line "brew: (not found)"
fi
