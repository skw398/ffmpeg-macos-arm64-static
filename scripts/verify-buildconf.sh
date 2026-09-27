#!/bin/sh
# verify-buildconf.sh — assert the built FFmpeg actually enabled every adopted
# library.
#
# `ffmpeg -buildconf` prints FFMPEG_CONFIGURATION, i.e. the configure argument
# string, so it only shows which flags were *passed*; configure can still warn
# and silently disable a library it cannot find. The real enablement lives in
# the generated config.h (CONFIG_<NAME>), which is what this script checks.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
SRC="${SRC:-$ROOT/build/src}"
PREFIX="${PREFIX:-$ROOT/build/prefix}"
BIN="$PREFIX/bin/ffmpeg"
TOKENS="$HERE/adopted-libs.txt"
CFG="$SRC/ffmpeg"

[ -x "$BIN" ] || { echo "ERROR: missing $BIN" >&2; exit 1; }
[ -f "$CFG/config.h" ] || { echo "ERROR: missing $CFG/config.h (run configure first)" >&2; exit 1; }

# diagnostics: surface why the binary aborts (stderr is otherwise discarded)
set +e
out=$("$BIN" -buildconf 2>&1); st=$?
echo "diag: ffmpeg -buildconf exit=$st"
printf '%s\n' "$out" | tail -n 8
set -e

fail=0

# enabled config macros (config.h: libraries/flags; config_components.h: components)
# written to a file: piping a large list into `grep -q` would abort the shell
# with EPIPE once grep exits early
enabled_file=$(mktemp)
trap 'rm -f "$enabled_file"' EXIT
awk '/^#define[ \t]+CONFIG_[A-Z0-9_]+[ \t]+1([ \t]|$)/{print $2}' \
  "$CFG/config.h" "$CFG/config_components.h" 2>/dev/null | sort -u > "$enabled_file"

# every adopted library must be enabled in config.h
for t in $(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$TOKENS"); do
  macro="CONFIG_$(printf '%s' "$t" | tr '[:lower:]' '[:upper:]')"
  if grep -qx "$macro" "$enabled_file"; then
    :
  else
    echo "DISABLED lib: $t ($macro)"; fail=1
  fi
done

# license flags passed / nonfree absent (configure args sanity)
BC=$("$BIN" -buildconf 2>/dev/null)
has_flag() { case "$BC" in *"$1"*) return 0 ;; *) return 1 ;; esac; }
has_flag --enable-gpl      || { echo "MISSING --enable-gpl"; fail=1; }
has_flag --enable-version3 || { echo "MISSING --enable-version3"; fail=1; }
if has_flag --enable-nonfree; then echo "UNEXPECTED --enable-nonfree"; fail=1; fi

# representative components actually present
comp() { # <list-flag> <regex> <label>
  if "$BIN" -"$1" 2>/dev/null | grep -qE "$2"; then :; else echo "MISSING $3"; fail=1; fi
}
comp encoders libx264     'libx264 encoder'
comp encoders libx265     'libx265 encoder'
comp encoders libsvtav1   'libsvtav1 encoder'
comp encoders librav1e    'librav1e encoder'
comp encoders libvpx      'libvpx encoder'
comp encoders libopenh264 'libopenh264 encoder'
comp encoders libopus     'libopus encoder'
comp encoders libmp3lame  'libmp3lame encoder'
comp decoders libdav1d    'libdav1d decoder'
comp filters  drawtext    'drawtext filter'
comp filters  subtitles   'subtitles filter'
comp filters  zscale      'zscale filter'
comp filters  libvmaf     'libvmaf filter'

if [ "$fail" -ne 0 ]; then echo "RESULT: buildconf check FAILED"; exit 1; fi
echo "RESULT: buildconf check passed"
