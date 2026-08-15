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

uv run python -m compileall -q backend tools
mkdir -p state/runtime
pytest_dir="state/runtime/pytest-$$"
migration_db="state/runtime/migrations-$$.db"
trap 'rm -rf -- "$pytest_dir"; rm -f -- "$migration_db"' EXIT
uv run python -m pytest --basetemp "$pytest_dir"
export DATABASE_URL="sqlite:///$migration_db"
uv run alembic upgrade head
uv run alembic check
