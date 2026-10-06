#!/bin/sh
# verify-build.sh — acceptance checks for a built FFmpeg (run on the CI runner).
# Checks distribution integration, not upstream algorithms or output quality.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
PREFIX="${PREFIX:-$ROOT/build/prefix}"
OUT="${OUT:-$ROOT/build/artifacts}"
BIN="$PREFIX/bin"
mkdir -p "$OUT"
# Filters execute in the fixture directory, so binary and log paths must be absolute.
BIN=$(cd "$BIN" && pwd)
OUT=$(cd "$OUT" && pwd)

fail=0

# Keep command output in the uploaded artifacts and show it on failure.
check() { # label command...
  label="$1"; shift
  if "$@" > "$OUT/$label.log" 2>&1; then
    echo "ok  $label"
    return 0
  else
    echo "ERROR: $label failed (log: $OUT/$label.log)"
    tail -n 80 "$OUT/$label.log"
    fail=1
    return 1
  fi
}

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
  check "$b.version" "$BIN/$b" -version || :
done

# --- otool: only Apple-provided dylibs ---
for b in ffmpeg ffprobe ffplay; do
  [ -x "$BIN/$b" ] || continue
  check "$b.otool" otool -L "$BIN/$b" || continue
  bad=$(tail -n +2 "$OUT/$b.otool.log" | awk '{print $1}' \
        | grep -vE '^(/usr/lib/|/System/Library/)' || true)
  if [ -n "$bad" ]; then
    echo "ERROR: $b depends on non-Apple libraries:"; echo "$bad"; fail=1
  else
    echo "ok  $b links only Apple libraries"
  fi
done

# --- buildconf and feature lists ---
check buildconf "$BIN/ffmpeg" -buildconf || :
cp "$OUT/buildconf.log" "$OUT/buildconf.txt"
for x in formats demuxers muxers codecs encoders decoders bsfs filters \
         protocols devices hwaccels pix_fmts sample_fmts layouts dispositions; do
  check "$x" "$BIN/ffmpeg" -"$x" || :
  cp "$OUT/$x.log" "$OUT/$x.txt"
done
echo "ok  saved buildconf and feature lists"

# --- encode -> decode smoke tests ---
T="$OUT/tests"; mkdir -p "$T"
smoke() { # name video-decoder-or-empty encoder-args...
  name="$1"; decoder="$2"; shift 2
  # Remove a previous run's file so it cannot satisfy the output check.
  rm -f "$T/$name"
  check "$name.encode" "$BIN/ffmpeg" -nostdin -y -loglevel error -xerror \
    "$@" "$T/$name" || return 0
  if [ ! -s "$T/$name" ]; then
    echo "ERROR: $name produced no file"; fail=1; return 0
  fi
  set --
  [ -z "$decoder" ] || set -- -c:v "$decoder"
  if check "$name.decode" "$BIN/ffmpeg" -nostdin -y -loglevel error -xerror \
     -abort_on empty_output "$@" -i "$T/$name" -f null -; then
    echo "ok  $name encode->decode"
  else
    echo "ERROR: $name encode->decode failed"; fail=1
  fi
}
smoke x264.mp4 "" -f lavfi -i testsrc=size=320x240:rate=30 -t 2 -c:v libx264 -pix_fmt yuv420p
smoke x265.mp4 "" -f lavfi -i testsrc=size=320x240:rate=30 -t 1 -c:v libx265 -pix_fmt yuv420p
smoke lame.mp3 "" -f lavfi -i sine=frequency=1000:sample_rate=44100 -t 2 -c:a libmp3lame
smoke aac.m4a "" -f lavfi -i sine=sample_rate=44100 -t 2 -c:a aac
smoke opus.ogg "" -f lavfi -i sine=sample_rate=48000 -t 2 -c:a libopus
smoke webp.webp "" -f lavfi -i testsrc=size=320x240:rate=1 -frames:v 1 -c:v libwebp

# Exercise all three adopted AV1 encoders and explicitly use the external decoder.
smoke av1-aom.ivf libdav1d -f lavfi -i testsrc2=size=320x240:rate=3 \
  -frames:v 3 -c:v libaom-av1 -cpu-used 8 -lag-in-frames 0 -threads 2
smoke av1-svt.ivf libdav1d -f lavfi -i testsrc2=size=320x240:rate=3 \
  -frames:v 3 -c:v libsvtav1 -preset 12 -threads 2
smoke av1-rav1e.ivf libdav1d -f lavfi -i testsrc2=size=320x240:rate=3 \
  -frames:v 3 -c:v librav1e -speed 10 -threads 2

# libsoxr has no entry in FFmpeg's component lists; explicitly select its engine.
smoke soxr.wav "" -f lavfi -i sine=frequency=1000:sample_rate=48000 -t 1 \
  -af aresample=44100:resampler=soxr -c:a pcm_s16le

# Probe actual media, requiring a stream to be reported, without testing metadata
# precision or ffprobe's complete output schema.
probe() { # name expected-stream-type
  name="$1"; kind="$2"
  if check "$name.probe" "$BIN/ffprobe" -v error -show_entries stream=codec_type \
     -of csv=p=0 "$T/$name"; then
    if ! grep -qx "$kind" "$OUT/$name.probe.log"; then
      echo "ERROR: ffprobe reported no $kind stream for $name"; fail=1
    fi
  fi
}
probe x264.mp4 video
probe aac.m4a audio

# Filter checks only require processing to complete and produce frames. No
# golden images, numerical accuracy assertions or quality-score thresholds.
filter() { # label filter-command-args...
  name="$1"; shift
  check "$name" sh -c 'cd "$1" || exit; shift; exec "$@"' sh "$T" \
    "$BIN/ffmpeg" -nostdin -loglevel error -xerror -abort_on empty_output "$@" || :
}
filter zscale \
  -f lavfi -i testsrc2=size=320x240:rate=1 -vf zscale=w=160:h=120 \
  -frames:v 1 -f null -

# Use the runner's Apple-provided font; do not download or redistribute a font.
font=/System/Library/Fonts/Supplemental/Arial.ttf
if [ -f "$font" ]; then
  filter drawtext \
    -f lavfi -i color=black:size=320x240:rate=1 \
    -vf "drawtext=fontfile=$font:text=Build smoke test:fontsize=24:fontcolor=white" \
    -frames:v 1 -f null -
else
  echo "ERROR: runner font missing: $font"; fail=1
fi
cat > "$T/subtitles.srt" <<'EOF'
1
00:00:00,000 --> 00:00:01,000
Build smoke test
EOF
# Relative filter paths avoid FFmpeg filter escaping of arbitrary workspace paths.
filter subtitles \
  -f lavfi -i color=black:size=320x240:rate=1 \
  -vf "subtitles=subtitles.srt:force_style='FontName=Arial,FontSize=24'" \
  -frames:v 1 -f null -
rm -f "$T/vmaf.json"
filter libvmaf \
  -f lavfi -i testsrc2=size=320x240:rate=3 \
  -filter_complex 'split=2[dist][ref];[dist][ref]libvmaf=log_fmt=json:log_path=vmaf.json' \
  -frames:v 3 -f null -
if [ ! -s "$T/vmaf.json" ]; then
  echo "ERROR: libvmaf produced no report"; fail=1
fi

# --- ffplay: start and play a short clip (headless) ---
if [ -x "$BIN/ffplay" ]; then
  if check ffplay.playback env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
     "$BIN/ffplay" -hide_banner -autoexit -nodisp -loglevel error \
     -f lavfi -i "sine=frequency=1000:duration=1"; then
    echo "ok  ffplay start/playback"
  else
    echo "ERROR: ffplay start/playback failed"; fail=1
  fi
fi

# --- checksums ---
( cd "$BIN" && shasum -a 256 ffmpeg ffprobe ffplay > "$OUT/SHA256SUMS" )

if [ "$fail" -ne 0 ]; then echo "RESULT: verify FAILED"; exit 1; fi
echo "RESULT: verify passed ($OUT)"
