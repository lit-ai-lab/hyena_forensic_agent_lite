#!/usr/bin/env bash
# Verify media paths and SHA-1 fingerprints against the adjacent index.
# Usage: bash verify_evidence.sh /path/to/HYENA_CTF_eng
set -u

ROOT=${1:?Usage: bash verify_evidence.sh DATASET_ROOT}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INDEX="$SCRIPT_DIR/ocr_index.tsv"

if [ ! -d "$ROOT" ]; then
  echo "dataset root is not a directory: $ROOT" >&2
  exit 2
fi
if [ ! -f "$INDEX" ]; then
  echo "index not found: $INDEX" >&2
  exit 2
fi

if command -v sha1sum >/dev/null 2>&1; then
  sha1_file() { sha1sum -- "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
  sha1_file() { shasum -a 1 -- "$1" | awk '{print $1}'; }
else
  echo "need sha1sum or shasum on PATH" >&2
  exit 2
fi

n=0
bad=0
while IFS=$'\t' read -r kind rel sha _; do
  [ "$kind" = "kind" ] && continue
  [ -n "$kind" ] || continue
  n=$((n + 1))
  file="$ROOT/$rel"
  if [ ! -f "$file" ]; then
    echo "missing: $rel"
    bad=$((bad + 1))
    continue
  fi
  actual=$(sha1_file "$file") || { echo "cannot hash: $rel"; bad=$((bad + 1)); continue; }
  if [ "$actual" != "$sha" ]; then
    echo "differs: $rel"
    bad=$((bad + 1))
  fi
done < "$INDEX"

echo "checked $n files; mismatched $bad"
[ "$n" -gt 0 ] && [ "$bad" -eq 0 ]
