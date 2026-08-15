#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI authentication is required; run 'gh auth login' or provide supported gh credentials." >&2
  exit 1
fi

remote_url="$(git remote get-url origin)"
default_repo="$(printf '%s' "$remote_url" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')"
repo="${1:-$default_repo}"
gh label create symphony --repo "$repo" --color 1D76DB --description "Eligible for autonomous Symphony implementation" --force
gh label create symphony-review --repo "$repo" --color 0E8A16 --description "Implementation complete; awaiting human review" --force
gh label create symphony-blocked --repo "$repo" --color D93F0B --description "External blocker requires human action" --force
echo "Symphony labels are ready in $repo."
