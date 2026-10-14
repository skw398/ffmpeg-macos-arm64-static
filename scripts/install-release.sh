#!/bin/sh
# Install the latest published macOS arm64 release; never execute its binaries.
set -eu

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = '--help' ] || [ "${1:-}" = '-h' ]; then
  printf 'Usage: sh install-release.sh [bin-directory]\nDefault: $HOME/.local/bin. Replaces ffmpeg, ffprobe and ffplay.\n'
  exit 0
fi
[ "$#" -le 1 ] || fail 'expected at most one installation directory'
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || fail 'requires macOS on Apple Silicon'

bin_dir="${1:-$HOME/.local/bin}"
repo_url='https://github.com/skw398/ffmpeg-macos-arm64-static'
tmp=$(mktemp -d)
staging=''
cleanup() {
  rm -rf "$tmp"
  if [ -n "$staging" ]; then rm -rf "$staging"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# Resolve once, then fetch both assets from that tag even if latest changes.
release_url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$repo_url/releases/latest")
case "$release_url" in
  "$repo_url"/releases/tag/*) version=${release_url##*/} ;;
  *) fail 'could not resolve the latest release tag' ;;
esac
case "$version" in
  ''|*[!A-Za-z0-9._-]*) fail 'invalid release tag' ;;
esac
package="ffmpeg-macos-arm64-static-$version"
archive="$package.tar.xz"
printf 'Downloading %s...\n' "$version"
curl -fsSL -o "$tmp/$archive" "$repo_url/releases/download/$version/$archive"
curl -fsSL -o "$tmp/SHA256SUMS" "$repo_url/releases/download/$version/SHA256SUMS"

expected=$(awk -v name="$archive" '
  $2 == name || $2 == "./" name { hash=$1; count++ }
  END {
    if (count != 1 || length(hash) != 64 || hash !~ /^[0-9a-fA-F]+$/) exit 1
    print hash
  }
' "$tmp/SHA256SUMS") || fail 'missing, duplicate or invalid archive checksum'
(cd "$tmp" && printf '%s  %s\n' "$expected" "$archive" | shasum -a 256 -c -) || fail 'SHA256 verification failed'

# Validate only the three selected regular files, and never extract share/ or
# other archive entries. No symlinks or duplicate binary entries are accepted.
listing=$(tar -tJvf "$tmp/$archive" \
  "$package/bin/ffmpeg" "$package/bin/ffprobe" "$package/bin/ffplay") || fail 'missing release binaries'
printf '%s\n' "$listing" | awk -v package="$package" '
  BEGIN {
    expected[package "/bin/ffmpeg"]=1
    expected[package "/bin/ffprobe"]=1
    expected[package "/bin/ffplay"]=1
  }
  {
    if (substr($1,1,1) != "-" || !($NF in expected) || seen[$NF]++) exit 1
    count++
  }
  END { if (count != 3) exit 1 }
' || fail 'missing, duplicate or non-regular release binaries'
mkdir "$tmp/unpack"
tar -xJf "$tmp/$archive" -C "$tmp/unpack" \
  "$package/bin/ffmpeg" "$package/bin/ffprobe" "$package/bin/ffplay"

for binary in ffmpeg ffprobe ffplay; do
  source="$tmp/unpack/$package/bin/$binary"
  [ -f "$source" ] && [ ! -L "$source" ] || fail "not a regular binary: $binary"
  header=$(od -An -tx1 -N8 "$source" | tr -d '[:space:]')
  [ "$header" = cffaedfe0c000001 ] || fail "not an arm64 Mach-O binary: $binary"
  [ ! -d "$bin_dir/$binary" ] || fail "installation target is a directory: $binary"
done

mkdir -p "$bin_dir"
staging=$(mktemp -d "$bin_dir/.ffmpeg-install.XXXXXX")
for binary in ffmpeg ffprobe ffplay; do
  cp "$tmp/unpack/$package/bin/$binary" "$staging/$binary"
  chmod 755 "$staging/$binary"
done
# Rename on the same filesystem; replace symlinks rather than their targets.
for binary in ffmpeg ffprobe ffplay; do
  mv -f "$staging/$binary" "$bin_dir/$binary"
done
printf 'Installed ffmpeg, ffprobe and ffplay (%s) in %s\n' "$version" "$bin_dir"
