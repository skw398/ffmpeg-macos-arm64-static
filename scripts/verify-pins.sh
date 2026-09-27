#!/bin/sh
# Verify every pin in deps.txt.
# - SHA-256 pins: fetch the URL and compare the hash.
# - COMMIT=<sha> pins (git): shallow-clone the tag/ref in the version column
#   from the repo URL and assert HEAD == <sha>. No vendored tarball is produced.
# - HASH-TODO / PIN-IN-CI: skipped (reported).
# Exits non-zero if any check fails.
# --skip disables verification (iteration only; never for release runs).
set -u

# --skip: disable verification
if [ "${1:-}" = "--skip" ]; then
  echo "SKIP: pin verification disabled (--skip)"
  exit 0
fi

HERE=$(cd "$(dirname "$0")" && pwd)
PINS="$HERE/../deps.txt"
DL="${1:-$HERE/../build/downloads}"
SRC="${SRC:-$HERE/../build/src}"
mkdir -p "$DL" "$SRC"
STATUS="$DL/.verify-status"
: > "$STATUS"
. "$HERE/lib.sh"

while IFS='|' read -r name version url hash license notes; do
  case "$name" in ""|\#*) continue ;; esac
  case "$hash" in
    HASH-TODO|PIN-IN-CI)
      echo "SKIP  $name (hash field: $hash)" >> "$STATUS"
      continue
      ;;
    COMMIT=*)
      sha=${hash#COMMIT=}
      # reuse the git-sources cache (build/src/<name>) when its HEAD matches;
      # re-clone only when it is missing or stale
      dest="$SRC/$name"
      actual=$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)
      if [ "$actual" != "$sha" ]; then
        rm -rf "$dest"
        fetch_commit "$name" "$version" "$url" "$sha" || true
        actual=$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)
      fi
      if [ "$actual" = "$sha" ]; then
        echo "OK    $name-$version (git HEAD=$sha)" >> "$STATUS"
      elif [ -n "$actual" ]; then
        echo "MISMATCH  $name-$version (git)" >> "$STATUS"
        echo "          expected: $sha" >> "$STATUS"
        echo "          actual:   $actual" >> "$STATUS"
      else
        echo "FAIL  $name  (git clone failed: $url ref $version)" >> "$STATUS"
      fi
      continue
      ;;
  esac
  file="$DL/$name-$version.tarball"
  actual=""
  [ -f "$file" ] && actual=$(shasum -a 256 "$file" | cut -d' ' -f1)
  if [ "$actual" != "$hash" ]; then
    # missing or stale (e.g. a regenerated upstream archive): fetch it again
    if ! curl -sSL --max-time 300 --fail -o "$file.tmp" "$url"; then
      echo "FAIL  $name  (download failed: $url)" >> "$STATUS"
      rm -f "$file.tmp"
      continue
    fi
    mv "$file.tmp" "$file"
    actual=$(shasum -a 256 "$file" | cut -d' ' -f1)
  fi
  if [ "$actual" = "$hash" ]; then
    echo "OK    $name-$version" >> "$STATUS"
  else
    echo "MISMATCH  $name-$version" >> "$STATUS"
    echo "          expected: $hash" >> "$STATUS"
    echo "          actual:   $actual" >> "$STATUS"
  fi
done < "$PINS"

cat "$STATUS"
fails=$(grep -c -E "^(FAIL|MISMATCH)" "$STATUS")
skips=$(grep -c "^SKIP" "$STATUS")
oks=$(grep -c "^OK" "$STATUS")
echo "RESULT: $oks ok, $skips skipped (TODO pins), $fails failed"
[ "$fails" -eq 0 ]
