#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v npm >/dev/null 2>&1; then
  echo "Required command not found: npm" >&2
  exit 1
fi

npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
