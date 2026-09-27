#!/bin/sh
# verify-build.sh — acceptance checks for a built FFmpeg (run on the CI runner).
# Checks: arm64, no non-Apple dylibs, feature lists, encode->decode smoke tests.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
PREFIX="${PREFIX:-$ROOT/build/prefix}"
OUT="${OUT:-$ROOT/build/artifacts}"
BIN="$PREFIX/bin"
mkdir -p "$OUT"

fail=0

# diagnostics: third-party paths embedded in the pkg-config files
if [ -d "$PREFIX/lib/pkgconfig" ]; then
  echo "== pkg-config files referencing /opt/homebrew:"
  grep -lE '/opt/homebrew' "$PREFIX/lib/pkgconfig"/*.pc 2>/dev/null || echo "  (none)"
  echo "== pkg-config files requiring libpng:"
  grep -lE 'libpng' "$PREFIX/lib/pkgconfig"/*.pc 2>/dev/null || echo "  (none)"
fi

# --- binaries exist and are arm64 ---
for b in ffmpeg ffprobe ffplay; do
  [ -x "$BIN/$b" ] || { echo "ERROR: missing $b"; fail=1; continue; }
  if file "$BIN/$b" | grep -q arm64; then
    echo "ok  $b is arm64"
  else
    echo "ERROR: $b is not arm64"; fail=1
  fi
  "$BIN/$b" -version > "$OUT/$b.version.txt" 2>&1 || true
done

# --- otool: only Apple-provided dylibs ---
for b in ffmpeg ffprobe ffplay; do
  [ -x "$BIN/$b" ] || continue
  bad=$(otool -L "$BIN/$b" | tail -n +2 | awk '{print $1}' \
        | grep -vE '^(/usr/lib/|/System/Library/)' || true)
  if [ -n "$bad" ]; then
    echo "ERROR: $b depends on non-Apple libraries:"; echo "$bad"; fail=1
  else
    echo "ok  $b links only Apple libraries"
  fi
done

# --- buildconf and feature lists ---
"$BIN/ffmpeg" -buildconf > "$OUT/buildconf.txt" 2>&1 || true
for x in formats demuxers muxers codecs encoders decoders parsers bsfs filters \
         protocols devices hwaccels pix_fmts sample_fmts layouts dispositions; do
  "$BIN/ffmpeg" -"$x" > "$OUT/$x.txt" 2>&1 || true
done
echo "ok  saved buildconf and feature lists"

# --- encode -> decode smoke tests ---
T="$OUT/tests"; mkdir -p "$T"
smoke() { # name encoder-args
  name="$1"; shift
  if "$BIN/ffmpeg" -y -loglevel error "$@" "$T/$name" >/dev/null 2>&1 \
     && "$BIN/ffmpeg" -y -loglevel error -i "$T/$name" -f null - >/dev/null 2>&1; then
    echo "ok  $name encode->decode"
  else
    echo "ERROR: $name encode->decode failed"; fail=1
  fi
}
smoke x264.mp4 -f lavfi -i testsrc=size=320x240:rate=30 -t 2 -c:v libx264 -pix_fmt yuv420p
smoke x265.mp4 -f lavfi -i testsrc=size=320x240:rate=30 -t 1 -c:v libx265 -pix_fmt yuv420p
smoke lame.mp3 -f lavfi -i sine=frequency=1000:sample_rate=44100 -t 2 -c:a libmp3lame
smoke aac.m4a  -f lavfi -i sine=sample_rate=44100 -t 2 -c:a aac
smoke opus.ogg -f lavfi -i sine=sample_rate=48000 -t 2 -c:a libopus
smoke webp.webp -f lavfi -i testsrc=size=320x240:rate=1 -frames:v 1 -c:v libwebp

# --- ffplay: start and play a short clip (headless) ---
if [ -x "$BIN/ffplay" ]; then
  if SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
     "$BIN/ffplay" -hide_banner -autoexit -nodisp -loglevel error \
     -f lavfi -i "sine=frequency=1000:duration=1" >/dev/null 2>&1; then
    echo "ok  ffplay start/playback"
  else
    echo "ERROR: ffplay start/playback failed"; fail=1
  fi
fi

# --- checksums ---
( cd "$BIN" && shasum -a 256 ffmpeg ffprobe ffplay > "$OUT/SHA256SUMS" )

if [ "$fail" -ne 0 ]; then echo "RESULT: verify FAILED"; exit 1; fi
echo "RESULT: verify passed ($OUT)"
