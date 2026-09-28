#!/bin/sh
# lib.sh — shared helpers for pin lookup and dependency fetch.
# Sourced by build-deps.sh, build-ffmpeg.sh and build-chromaprint.sh; the caller
# must set HERE, PINS, DL and SRC before sourcing.

# pin <name> -> "version|url|hash" from the pin list
pin() {
  awk -F'|' -v n="$1" '$1 == n { print $2 "|" $3 "|" $4; exit }' "$PINS"
}

# download <name> -> ensure the pinned tarball is present in $DL and verified,
# without extracting it (tarball pins only; commit pins are a no-op)
download() {
  name="$1"
  p=$(pin "$name")
  [ -n "$p" ] || { echo "ERROR: no pin for $name" >&2; exit 1; }
  version=$(printf '%s' "$p" | cut -d'|' -f1)
  url=$(printf '%s' "$p" | cut -d'|' -f2)
  hash=$(printf '%s' "$p" | cut -d'|' -f3)
  case "$hash" in
    COMMIT=*) return 0 ;;
  esac
  file="$DL/$name-$version.tarball"
  # a stale cached tarball (e.g. an archive regenerated upstream, or a cache
  # from a previous URL) must not be trusted: re-download on hash mismatch
  if [ -f "$file" ] && [ "$(shasum -a 256 "$file" | cut -d' ' -f1)" != "$hash" ]; then
    echo "== re-downloading $name (cached tarball hash mismatch)" >&2
    rm -f "$file"
  fi
  if [ ! -f "$file" ]; then
    curl -sSL --fail --max-time 300 -o "$file.tmp" "$url"
    mv "$file.tmp" "$file"
  fi
  actual=$(shasum -a 256 "$file" | cut -d' ' -f1)
  [ "$actual" = "$hash" ] || { echo "ERROR: $name hash mismatch" >&2; exit 1; }
}

# fetch_commit <name> <ref> <url> <sha> -> ensure $SRC/<name> is checked out at
# <sha>. <ref> is usually a tag; for repositories without a tag at the pinned
# commit it is the 40-hex commit itself (fetched directly). Returns non-zero on
# mismatch. The checkout is host-agnostic (works for GitHub, GitLab, ...).
fetch_commit() {
  name="$1"; ref="$2"; url="$3"; sha="$4"
  dest="$SRC/$name"
  # a cached tree at another commit (e.g. a pin bump, where restore-keys can
  # bring back a cache keyed by the previous deps.txt) must be re-cloned, or the
  # checkout below would keep the old HEAD and fail with a commit mismatch
  if [ -d "$dest/.git" ] \
     && [ "$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)" != "$sha" ]; then
    rm -rf "$dest"
  fi
  if [ ! -d "$dest/.git" ]; then
    if printf '%s' "$ref" | grep -qE '^[0-9a-f]{40}$'; then
      mkdir -p "$dest"
      git -C "$dest" init -q
      git -C "$dest" remote add origin "$url"
      git -C "$dest" fetch -q --depth 1 origin "$ref"
      git -C "$dest" checkout -q FETCH_HEAD
    else
      git clone -q --depth 1 --branch "$ref" "$url" "$dest"
    fi
  fi
  # discard in-place edits: the git-source cache stores the tree after the
  # previous run's -Werror strip and source patches, which must not accumulate
  git -C "$dest" checkout -q -- . 2>/dev/null || true
  actual=$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)
  [ "$actual" = "$sha" ]
}

# fetch <name> -> verify and extract (or clone) into $SRC/<name>
fetch() {
  name="$1"
  p=$(pin "$name")
  [ -n "$p" ] || { echo "ERROR: no pin for $name" >&2; exit 1; }
  version=$(printf '%s' "$p" | cut -d'|' -f1)
  url=$(printf '%s' "$p" | cut -d'|' -f2)
  hash=$(printf '%s' "$p" | cut -d'|' -f3)
  dest="$SRC/$name"

  case "$hash" in
    COMMIT=*)
      sha=${hash#COMMIT=}
      fetch_commit "$name" "$version" "$url" "$sha" \
        || { echo "ERROR: $name commit mismatch" >&2; exit 1; }
      ;;
    *)
      download "$name"
      file="$DL/$name-$version.tarball"
      rm -rf "$dest"; mkdir -p "$dest"
      tar -xf "$file" -C "$dest" --strip-components=1
      ;;
  esac

  # NOTE: a generic strip of bare -Werror from CMake files used to live here.
  # Its occurrence count came out 0 for every dependency, so it was removed as
  # dead weight (see docs/BUILD-WORKAROUNDS.md); re-add it if a dependency
  # starts promoting warnings to errors.
}

# --- workaround trials -------------------------------------------------------
# NO_WORKAROUNDS (space/comma separated ids, see scripts/trial-workarounds.txt)
# names workarounds to leave out, so a CI trial run can show whether they are
# still needed. Trial runs are uncached and never saved, so a trial-built prefix
# cannot mask or poison a normal build (see docs/BUILD-WORKAROUNDS.md).
TRIALS="${TRIALS:-$HERE/trial-workarounds.txt}"

# trial_disabled <id> -> true when this run leaves the workaround <id> out
trial_disabled() {
  case " $(printf '%s' "${NO_WORKAROUNDS:-}" | tr ',' ' ') " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

# trial_flag <id> <value...> -> the value, unless <id> is disabled (then nothing,
# with a note on stderr). Keeps a workaround switchable at its use site.
trial_flag() {
  if trial_disabled "$1"; then
    echo "== trial: dropping workaround '$1'" >&2
    return 0
  fi
  shift
  printf '%s' "$*"
}

# validate_trials -> announce the run's trial and reject an unknown id: a typo
# would leave every workaround in place and make the result meaningless.
# `reset` is reserved: it leaves every workaround in place but still makes the
# run reset the cached build state, which a manual per-library flag trial needs
# (see docs/BUILD-WORKAROUNDS.md).
validate_trials() {
  ids=$(printf '%s' "${NO_WORKAROUNDS:-}" | tr ',' ' ' | tr -s ' ' | sed 's/^ *//;s/ *$//')
  [ -n "$ids" ] || return 0
  for id in $ids; do
    [ "$id" = reset ] && continue
    grep -qE "^$id\|" "$TRIALS" || {
      echo "ERROR: unknown trial workaround id '$id' (see $TRIALS)" >&2
      echo "       known ids:" >&2
      grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$TRIALS" | cut -d'|' -f1 | sed 's/^/         /' >&2
      exit 1
    }
  done
  echo "== trial run: leaving out workarounds: $ids"
}
