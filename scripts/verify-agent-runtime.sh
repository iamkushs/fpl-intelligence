#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

allow_missing_github_auth=false
if [[ "${1:-}" == "--allow-missing-github-auth" ]]; then
  allow_missing_github_auth=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--allow-missing-github-auth]" >&2
  exit 2
fi

runtime_file="tooling/symphony-runtime.env"
if [[ ! -f "$runtime_file" ]]; then
  echo "Missing runtime configuration: $runtime_file" >&2
  exit 1
fi

# This tracked file contains no secrets and is intentionally shell-compatible.
# shellcheck disable=SC1090
source "$runtime_file"

failed=0
report_command() {
  local name="$1"
  shift
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$name: MISSING"
    failed=1
    return
  fi
  echo "$name: $("$@" 2>&1 | head -n 1)"
}

echo "platform: $(uname -srm 2>/dev/null || echo unknown)"
echo "shell: ${BASH_VERSION:-unknown}"
report_command git git --version
report_command gh gh --version
report_command codex codex --version
report_command node node --version
report_command npm npm --version
report_command python python --version
report_command uv uv --version
if command -v uv >/dev/null 2>&1; then
  echo "project-python: $(uv run python --version 2>&1 | head -n 1)"
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo "github-auth: available"
else
  echo "github-auth: unavailable"
  if [[ "$allow_missing_github_auth" != true ]]; then
    failed=1
  fi
fi

installed_codex=""
if command -v codex >/dev/null 2>&1; then
  installed_codex="$(codex --version 2>/dev/null | awk '{print $NF}')"
fi

if [[ "${CODEX_VERSION:-UNTESTED}" == "UNTESTED" || -z "${CODEX_VERSION:-}" ]]; then
  echo "codex-tested-version: UNTESTED (pin after a successful end-to-end smoke test)"
elif [[ "$installed_codex" != "$CODEX_VERSION" ]]; then
  echo "Codex version mismatch: expected $CODEX_VERSION, found ${installed_codex:-missing}" >&2
  failed=1
else
  echo "codex-tested-version: $CODEX_VERSION (matches)"
fi

echo "symphony-implementation: ${SYMPHONY_IMPLEMENTATION:-unknown}"
if [[ "${SYMPHONY_REF:-UNTESTED}" == "UNTESTED" || "${SYMPHONY_REF:-}" == "UNPINNED" || -z "${SYMPHONY_REF:-}" ]]; then
  echo "symphony-ref: UNTESTED (record after a successful end-to-end smoke test)"
else
  echo "symphony-ref: $SYMPHONY_REF"
fi

if [[ -n "${SYMPHONY_SOURCE_DIR:-}" && -d "$SYMPHONY_SOURCE_DIR/.git" ]]; then
  echo "symphony-local-head: $(git -C "$SYMPHONY_SOURCE_DIR" rev-parse HEAD)"
else
  echo "symphony-local-source: not configured"
fi

exit "$failed"
