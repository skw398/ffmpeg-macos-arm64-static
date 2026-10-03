#!/bin/sh
# plan-workaround-runs.sh — decide which workaround checks a run should do.
#
# Prints GitHub Actions outputs:
#   trials=<JSON array of {"id":..., "fast":...}>   (for a matrix)
#   install_rules=true|false
#
# Inputs (env):
#   EVENT         push | schedule | workflow_dispatch
#   BEFORE        push only: the pre-push commit
#   INPUT_TRIALS  workflow_dispatch only: space/comma separated ids
#   INPUT_INSTALL workflow_dispatch only: true to run the install-rule check
#   GITHUB_OUTPUT where the outputs go (defaults to stdout)
#
# A push that changes deps.txt selects the per-library trials whose library's pin
# changed; a schedule selects the global ids (which cannot use a fast trial).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HERE/.."
TRIALS="$HERE/trial-workarounds.txt"

emit() { printf '%s=%s\n' "$1" "$2" >> "${GITHUB_OUTPUT:-/dev/stdout}"; }

ids() { grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$TRIALS" | cut -d'|' -f1; }
libs_of() { awk -F'|' -v n="$1" '$1 == n { print $4; exit }' "$TRIALS"; }

# libraries whose pin changed in this push (empty when deps.txt is untouched)
changed_libs() {
  [ -n "${BEFORE:-}" ] || return 0
  case "$BEFORE" in 0000000*) return 0 ;; esac
  git -C "$ROOT" diff "$BEFORE" HEAD -- deps.txt 2>/dev/null \
    | grep -E '^[+-][a-zA-Z0-9]' | sed 's/^[+-]//' | cut -d'|' -f1 | sort -u
}

selected=""
case "${EVENT:-workflow_dispatch}" in
  workflow_dispatch)
    selected=$(printf '%s' "${INPUT_TRIALS:-}" | tr ',' ' ')
    ;;
  push)
    changed=$(changed_libs | tr '\n' ' ')
    for id in $(ids); do
      libs=$(libs_of "$id")
      [ "$libs" = '*' ] && continue
      for l in $libs; do
        case " $changed " in *" $l "*) selected="$selected $id"; break ;; esac
      done
    done
    ;;
  schedule)
    for id in $(ids); do
      [ "$(libs_of "$id")" = '*' ] && selected="$selected $id"
    done
    ;;
esac

# matrix JSON: fast is possible unless the id affects every library
json="["
sep=""
for id in $selected; do
  [ -n "$id" ] || continue
  fast=true
  [ "$(libs_of "$id")" = '*' ] && fast=false
  json="$json$sep{\"id\":\"$id\",\"fast\":$fast}"
  sep=","
done
json="$json]"
emit trials "$json"

install_rules=false
[ "${EVENT:-}" = schedule ] && install_rules=true
[ "${INPUT_INSTALL:-false}" = true ] && install_rules=true
emit install_rules "$install_rules"

echo "== workaround run plan: trials=$json install_rules=$install_rules"
