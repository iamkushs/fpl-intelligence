#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

expected=$'AGENTS.md\nWORKFLOW.md'
actual="$(git ls-files '*.md' | LC_ALL=C sort)"

if [[ "$actual" != "$expected" ]]; then
  echo "Tracked Markdown policy violation." >&2
  echo "Expected:" >&2
  printf '%s\n' "$expected" >&2
  echo "Actual:" >&2
  printf '%s\n' "$actual" >&2
  exit 1
fi

echo "Tracked Markdown policy verified."
