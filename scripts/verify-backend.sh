#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for command_name in uv python; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command not found: $command_name" >&2
    exit 1
  fi
done

uv run python -m compileall -q backend
uv run python -m pytest

mkdir -p state/runtime
migration_db="state/runtime/migrations-$$.db"
trap 'rm -f -- "$migration_db"' EXIT
export DATABASE_URL="sqlite:///$migration_db"
uv run alembic upgrade head
uv run alembic check
