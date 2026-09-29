#!/bin/sh
# report-build-flags.sh — triage the per-library build flags and report the ones
# that are already inert, so they can be trimmed (report only; never fails).
#
# Signals:
#   1. autotools "unrecognized options": configure rejected the flag
#   2. CMake "unused-cli": the project never reads the -D variable
#   3. meson: the -D value already equals the option's declared default
#   4. patch/transform activity printed by build-deps.sh: a transform that
#      matches 0 occurrences can be dropped
#
# Flags passed to every library by build-deps.sh (BUILD_SHARED_LIBS, BUILD_TESTING,
# CMAKE_*) are labelled [global]; anything else is a per-library trim candidate.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
RECIPES="$HERE/build-deps.txt"
PINS="$ROOT/deps.txt"
SRC="${SRC:-$ROOT/build/src}"
DL="${DL:-$ROOT/build/downloads}"
LOGS="${LOGS:-$ROOT/build/logs}"

GLOBAL_CMAKE="CMAKE_INSTALL_PREFIX CMAKE_BUILD_TYPE CMAKE_PREFIX_PATH CMAKE_POLICY_VERSION_MINIMUM CMAKE_OSX_DEPLOYMENT_TARGET BUILD_SHARED_LIBS BUILD_TESTING"

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD"; }
trap cleanup EXIT

[ -d "$LOGS" ] || { echo "ERROR: no build logs at $LOGS (run build-deps.sh first)" >&2; exit 1; }

# normalise a boolean-ish flag value: meson accepts enabled/disabled for booleans
norm_bool() {
  case "$1" in
    true|enabled)  printf 'true' ;;
    false|disabled) printf 'false' ;;
    *)             printf '%s' "$1" ;;
  esac
}

# print "name=value" for every option declared in a meson options file
meson_option_defaults() { # <file>
  tr '\n' ' ' < "$1" | awk '{ gsub(/option\(/, "\noption("); print }' \
    | while IFS= read -r line; do
        name=$(printf '%s' "$line" | sed -n "s/^option('\([^']*\)'.*/\1/p")
        [ -n "$name" ] || continue
        val=$(printf '%s' "$line" | sed -n "s/.*value[[:space:]]*:[[:space:]]*'\{0,1\}\([A-Za-z0-9_.-]*\)'\{0,1\}.*/\1/p")
        [ -n "$val" ] || continue
        printf '%s=%s\n' "$name" "$(norm_bool "$val")"
      done
}

# locate the meson options file for a recipe (git checkout first, else tarball).
# Search subdirectories too: libvmaf keeps its meson build in libvmaf/.
meson_options_file() { # <name> <version> <hash>
  name="$1"; ver="$2"; hash="$3"
  for f in $(find "$SRC/$name" -maxdepth 2 \( -name meson_options.txt -o -name meson.options \) 2>/dev/null); do
    printf '%s\n' "$f"; return 0
  done
  case "$hash" in COMMIT=*|"") return 1 ;; esac
  tb="$DL/$name-$ver.tarball"
  [ -f "$tb" ] || return 1
  p=$(tar -tf "$tb" 2>/dev/null | grep -E '(^|/)meson_options\.txt$|(^|/)meson\.options$' | head -1)
  [ -n "$p" ] || return 1
  [ -n "$TMPD" ] || TMPD=$(mktemp -d)
  tar -xOf "$tb" "$p" > "$TMPD/meson-opts" 2>/dev/null || return 1
  printf '%s\n' "$TMPD/meson-opts"
}

# --- 1. autotools: flags the configure script rejected -------------------------
echo "== autotools flags rejected by configure (unrecognized options) =="
found=0
for f in "$LOGS"/*.log; do
  [ -f "$f" ] || continue
  opts=$(grep -h 'unrecognized options:' "$f" 2>/dev/null \
    | sed 's/.*unrecognized options:[[:space:]]*//' | tr ',' '\n' \
    | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$' | sort -u | tr '\n' ' ')
  if [ -n "$opts" ]; then printf '%-16s %s\n' "$(basename "$f" .log)" "$opts"; found=1; fi
done
[ "$found" -eq 0 ] && echo "  (none)"

# --- 2. CMake: -D variables the project never read ----------------------------
echo
echo "== CMake -D variables reported as unused (unused-cli) =="
found=0
gcount=""
for f in "$LOGS"/*.log; do
  [ -f "$f" ] || continue
  vars=$(awk '/CMake Warning \(unused-cli\)/{u=1;next} u&&/^[[:space:]]+[A-Za-z_][A-Za-z0-9_]*$/{print $1;next} u&&NF==0{next} u&&!/^[[:space:]]/{u=0}' "$f" 2>/dev/null | sort -u)
  [ -n "$vars" ] || continue
  lib=$(basename "$f" .log)
  for v in $vars; do
    case " $GLOBAL_CMAKE " in
      *" $v "*) gcount="$gcount $v" ;;
      *)        printf '%-16s %-30s [per-lib] trim candidate\n' "$lib" "$v"; found=1 ;;
    esac
  done
done
[ "$found" -eq 0 ] && echo "  (no per-library candidates)"
if [ -n "$gcount" ]; then
  printf '  [global] also unused somewhere (expected): '
  printf '%s\n' $gcount | sort | uniq -c | awk '{ printf "%s in %s library/libraries; ", $2, $1 }'
  echo
fi

# --- 3. meson: -D values that already equal the declared default --------------
echo
echo "== meson -D flags equal to the declared default (redundant) =="
hits=$(mktemp)
grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$RECIPES" | while IFS='|' read -r name kind flags note; do
  name=$(printf '%s' "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  kind=$(printf '%s' "$kind" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  flags=$(printf '%s' "$flags" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [ "$kind" = meson ] || [ "$kind" = custom ] || continue
  ver=$(awk -F'|' -v n="$name" '$1 == n { print $2; exit }' "$PINS")
  hash=$(awk -F'|' -v n="$name" '$1 == n { print $4; exit }' "$PINS")
  ofile=$(meson_options_file "$name" "$ver" "$hash") || continue
  defaults=$(meson_option_defaults "$ofile")
  for fl in $flags; do
    case "$fl" in -D*=*) ;; *) continue ;; esac
    opt=${fl#-D}; opt=${opt%%=*}
    val=${fl#-D$opt=}
    dflt=$(printf '%s\n' "$defaults" | sed -n "s/^$opt=//p" | head -1)
    [ -n "$dflt" ] || continue
    if [ "$(norm_bool "$val")" = "$dflt" ]; then
      printf '%-16s %-34s (default %s) trim candidate\n' "$name" "$fl" "$dflt" >> "$hits"
    fi
  done
done
if [ -s "$hits" ]; then cat "$hits"; else echo "  (none)"; fi
rm -f "$hits"

# --- 4. transform counts from build-deps.sh ----------------------------------
echo
echo "== patch/transform activity (a transform with 0 occurrences is droppable) =="
found=0
for f in "$LOGS"/*.log; do
  [ -f "$f" ] || continue
  c=$(grep -h '^== patch' "$f" 2>/dev/null | sort -u | tr '\n' '; ')
  if [ -n "$c" ]; then printf '%-16s %s\n' "$(basename "$f" .log)" "$c"; found=1; fi
done
[ "$found" -eq 0 ] && echo "  (none)"

echo
echo "RESULT: report only (nothing here fails the build)"
