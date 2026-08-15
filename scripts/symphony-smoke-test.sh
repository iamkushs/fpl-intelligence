#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

remote_url="$(git remote get-url origin)"
repo="$(printf '%s' "$remote_url" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')"
wait_seconds=0
record_runtime=false
symphony_ref=""

usage() {
  echo "Usage: $0 [--repo OWNER/REPO] [--wait SECONDS] [--record-runtime --symphony-ref REF]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --wait) wait_seconds="$2"; shift 2 ;;
    --record-runtime) record_runtime=true; shift ;;
    --symphony-ref) symphony_ref="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for command_name in gh codex git jq; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Required command not found: $command_name" >&2; exit 1; }
done
gh auth status >/dev/null 2>&1 || { echo "GitHub CLI authentication is required." >&2; exit 1; }
[[ "$wait_seconds" =~ ^[0-9]+$ ]] || { echo "--wait must be a non-negative integer." >&2; exit 2; }
if [[ "$record_runtime" == true && -z "$symphony_ref" ]]; then
  echo "--record-runtime requires --symphony-ref REF." >&2
  exit 2
fi

./scripts/setup-symphony-github.sh "$repo"

issue_url="$(gh issue create --repo "$repo" --title "Symphony smoke test: verification-only handoff" --label symphony --body "This is an opt-in orchestration smoke test. Do not modify product behavior. Maintain exactly one ## Codex Workpad comment. Change only tooling/symphony-smoke.env so SMOKE_TEST_SEQUENCE equals this issue number, run ./scripts/verify-all.sh, merge origin/main into the issue branch, push it, open a PR whose body references this issue URL, update the workpad with evidence, remove the symphony label, and add symphony-review. Keep this issue open and do not merge the PR.")"
issue_number="${issue_url##*/}"
echo "Created smoke-test issue: $issue_url"

if [[ "$wait_seconds" -eq 0 ]]; then
  echo "Smoke test started but not monitored. Re-run with --wait SECONDS to monitor a new test."
  exit 0
fi

deadline=$((SECONDS + wait_seconds))
while (( SECONDS < deadline )); do
  issue_json="$(gh issue view "$issue_number" --repo "$repo" --json state,labels,comments)"
  has_review="$(printf '%s' "$issue_json" | jq -r '[.labels[].name] | index("symphony-review") != null')"
  has_active="$(printf '%s' "$issue_json" | jq -r '[.labels[].name] | index("symphony") != null')"
  has_workpad="$(printf '%s' "$issue_json" | jq -r '[.comments[].body | startswith("## Codex Workpad")] | any')"
  pr_count="$(gh pr list --repo "$repo" --state open --search "$issue_url in:body" --json number --jq 'length')"
  if [[ "$has_review" == true && "$has_active" == false && "$has_workpad" == true && "$pr_count" -gt 0 ]]; then
    echo "Smoke test passed: workpad, PR, and symphony-review handoff observed."
    if [[ "$record_runtime" == true ]]; then
      codex_version="$(codex --version | awk '{print $NF}')"
      printf '# Recorded only after a successful end-to-end Symphony smoke test.\nSYMPHONY_REF=%s\nCODEX_VERSION=%s\n' "$symphony_ref" "$codex_version" > tooling/symphony-runtime.env
      echo "Recorded Symphony ref $symphony_ref and Codex version $codex_version. Commit this update after review."
    fi
    exit 0
  fi
  sleep 15
done

echo "Smoke test did not reach review handoff within ${wait_seconds}s; inspect $issue_url and Symphony logs." >&2
exit 1
