#!/bin/sh
# lib-consumers.sh — map FFmpeg external libraries to their consumer components
# and check that every library we enable actually turns one on. This is the 2nd
# step of the adoption rule in docs/COMPONENT-AUDIT.md (4 conditions AND an
# enabled consumer). Consumers come from configure:
#   hard    = *_deps / *_select / *_deps_any   (component requires the lib)
#   suggest = *_suggest                        (optional enhancement)
#
# Usage:
#   lib-consumers.sh [configure-file]            print "lib|hard|suggest" for all libs
#   lib-consumers.sh --check [options] <lib>...  fail if a lib has no consumer
#     --disabled "comp ..."    pre-configure: components this build will not enable
#     --config-header <path>   post-configure: read config.h /
#                              config_components.h (a directory expands to both)
#     --binary <ffmpeg>        post-build (preferred): read the artifact's own
#                              -encoders/-decoders/... lists, i.e. verify the
#                              shipped binary, not just the build configuration
#
# Without a configure file it is extracted from the pinned FFmpeg tarball in
# deps.txt. It only parses configure/headers/lists; it does not build FFmpeg.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
TMP=""
TMPD=""
cleanup() { [ -n "$TMP" ] && rm -f "$TMP"; [ -n "$TMPD" ] && rm -rf "$TMPD"; }
trap cleanup EXIT

resolve_configure() {
  if [ -n "${1:-}" ]; then
    printf '%s\n' "$1"
    return 0
  fi
  ver=$(awk -F'|' '$1=="ffmpeg"{print $2}' "$ROOT/deps.txt")
  tb="$ROOT/build/downloads/ffmpeg-$ver.tarball"
  if [ ! -f "$tb" ]; then
    echo "ERROR: missing pinned FFmpeg tarball: $tb" >&2
    exit 2
  fi
  TMP=$(mktemp)
  if ! tar -xOf "$tb" "ffmpeg-$ver/configure" > "$TMP"; then
    echo "ERROR: cannot extract configure from $tb" >&2
    exit 2
  fi
  printf '%s\n' "$TMP"
}

# Emit "lib|hard|suggest" for every external library in configure.
map_consumers() {
  # shellcheck disable=SC2016
  awk '
    BEGIN { inlist = 0 }
    /^(EXTERNAL_LIBRARY_LIST|EXTERNAL_LIBRARY_GPL_LIST|EXTERNAL_LIBRARY_NONFREE_LIST|EXTERNAL_LIBRARY_VERSION3_LIST|EXTERNAL_LIBRARY_GPLV3_LIST)="/ { inlist = 1; next }
    inlist && /^"$/ { inlist = 0; next }
    inlist {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^\$/) continue
        if (!($i in libs)) { libs[$i] = 1; liborder[++L] = $i }
      }
      next
    }
    /^[A-Za-z0-9_]+_(deps|select|deps_any|suggest)=/ {
      eq = index($0, "=")
      key = substr($0, 1, eq - 1)
      if (key ~ /_suggest$/) { sub(/_suggest$/, "", key); k = 2 }
      else { sub(/_(deps|select|deps_any)$/, "", key); k = 1 }
      val = substr($0, eq + 1)
      gsub(/"/, "", val)
      n = split(val, toks, /[ \t]+/)
      for (j = 1; j <= n; j++) {
        t = toks[j]
        if (t in libs) {
          if (k == 1) hard[t] = hard[t] (hard[t] ? " " : "") key
          else        sug[t]  = sug[t]  (sug[t]  ? " " : "") key
        }
      }
    }
    END { for (i = 1; i <= L; i++) print liborder[i] "|" hard[liborder[i]] "|" sug[liborder[i]] }
  ' "$1"
}

mode="map"
disabled=""
config_headers=""
binary=""
if [ "${1:-}" = "--check" ]; then
  mode="check"
  shift
  while :; do
    case "${1:-}" in
      --disabled)      disabled="$2"; shift 2 ;;
      --config-header) config_headers="$config_headers $2"; shift 2 ;;
      --binary)        binary="$2"; shift 2 ;;
      *) break ;;
    esac
  done
fi

if [ "$mode" = "map" ]; then
  CFG=$(resolve_configure "${1:-}")
  map_consumers "$CFG"
  exit 0
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 --check [--disabled \"comp ...\"] [--config-header <path>] [--binary <ffmpeg>] <lib> [<lib> ...]" >&2
  exit 2
fi

if [ -n "$binary" ] && [ ! -x "$binary" ]; then
  echo "ERROR: --binary is not executable: $binary" >&2
  exit 2
fi

# Expand --config-header arguments: directories become both generated headers.
header_files=""
for p in $config_headers; do
  if [ -d "$p" ]; then
    header_files="$header_files $p/config.h $p/config_components.h"
  else
    header_files="$header_files $p"
  fi
done

where="enabled (pre-configure)"
if [ -n "$binary" ]; then
  where="listed by the binary"
elif [ -n "$header_files" ]; then
  where="enabled in config.h"
fi

enabled_macros=""
if [ -n "$header_files" ]; then
  # shellcheck disable=SC2086
  enabled_macros=$(awk '/^#define[ \t]+CONFIG_[A-Z0-9_]+[ \t]+1([ \t]|$)/ { print $2 }' $header_files | sort -u)
fi

# --- artifact mode: read the component names the shipped binary really has ---
# A consumer component (e.g. "libx264_encoder", "dash_demuxer", "caca_outdev")
# maps to an `ffmpeg -<list>` entry, so a library is confirmed present only if
# the binary itself lists one of its consumers.
component_to_list() { # <component> -> "<list> <name>" (nothing if not a list item)
  c=$1
  # demuxer before muxer: "dash_demuxer" also ends in "_muxer"
  for pair in encoder:encoders decoder:decoders filter:filters demuxer:demuxers \
              muxer:muxers parser:parsers bsf:bsfs protocol:protocols \
              indev:devices outdev:devices hwaccel:hwaccels; do
    suf=${pair%%:*}; list=${pair##*:}
    case "$c" in
      *_$suf) printf '%s %s\n' "$list" "${c%_$suf}"; return 0 ;;
    esac
  done
  return 1
}

# names listed by `ffmpeg -<list>`, cached per list. Header/legend lines are
# skipped; the first field is a flags column (e.g. "V....D", "DE", "...") when a
# name follows it, otherwise the line is a bare name (protocols, bsfs, hwaccels).
list_names() { # <list>
  [ -n "$TMPD" ] || TMPD=$(mktemp -d)
  cache="$TMPD/$1"
  if [ ! -f "$cache" ]; then
    "$binary" -hide_banner -"$1" 2>/dev/null | awk '
      /^[A-Za-z][A-Za-z ]*:$/       { next }
      /^[[:space:]]*[A-Za-z.|]+ = / { next }
      /^-+$/                        { next }
      NF == 0                       { next }
      {
        if ($1 ~ /^[A-Z.]+$/ && NF >= 2) print $2
        else                             print $1
      }
    ' | sort -u > "$cache"
  fi
  cat "$cache"
}

consumer_in_binary() { # <component>
  pair=$(component_to_list "$1") || return 1
  set -- $pair
  list_names "$1" | grep -qxF -- "$2"
}

consumer_enabled() {
  c=$1
  if [ -n "$binary" ]; then
    consumer_in_binary "$c"
  elif [ -n "$header_files" ]; then
    macro="CONFIG_$(printf '%s' "$c" | tr '[:lower:]' '[:upper:]')"
    printf '%s\n' "$enabled_macros" | grep -qx "$macro"
  else
    for d in $disabled; do
      [ "$c" = "$d" ] && return 1
    done
    return 0
  fi
}

CFG=$(resolve_configure "")
MAP=$(map_consumers "$CFG")

# artifact mode: explicit per-library entries (registered names differ from the
# configure component name) from the data file, keyed by library token
overrides=""
if [ -n "$binary" ]; then
  ovf="${OVERRIDES:-$HERE/artifact-consumers.txt}"
  [ -f "$ovf" ] && overrides=$(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$ovf")
fi

status=0
skipped=0
for lib in "$@"; do
  if [ -n "$binary" ]; then
    ov=$(printf '%s\n' "$overrides" | awk -F'|' -v l="$lib" '$1 == l { print $2 "|" $3 "|" $4 }')
    if [ -n "$ov" ]; then
      kind=${ov%%|*}; rest=${ov#*|}
      olist=${rest%%|*}; oname=${rest#*|}
      if [ "$kind" = skip ]; then
        echo "skip         $lib ($oname)"
        skipped=$((skipped + 1))
      elif list_names "$olist" | grep -qxF -- "$oname"; then
        echo "ok           $lib -> $olist:$oname"
      else
        echo "NO ARTIFACT  $lib (missing $olist:$oname)"
        status=1
      fi
      continue
    fi
  fi
  line=$(printf '%s\n' "$MAP" | awk -F'|' -v l="$lib" '$1 == l { print $2 "|" $3 }')
  hard=${line%%|*}
  sug=${line##*|}
  hard_ok=""
  sug_ok=""
  for c in $hard; do consumer_enabled "$c" && hard_ok="$hard_ok $c"; done
  for c in $sug; do consumer_enabled "$c" && sug_ok="$sug_ok $c"; done
  if [ -n "${hard_ok# }" ]; then
    echo "ok           $lib ->${hard_ok}"
  elif [ -n "${sug_ok# }" ]; then
    echo "ok (suggest) $lib ->${sug_ok}"
  else
    echo "NO CONSUMER  $lib"
    status=1
  fi
done
if [ "$status" -eq 0 ]; then
  echo "RESULT: all checked libraries have a consumer $where"
  [ "$skipped" -gt 0 ] && echo "        ($skipped library/libraries not confirmable from ffmpeg lists)"
fi
exit "$status"
