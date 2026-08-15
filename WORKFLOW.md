---
tracker:
  kind: github
  provider:
    repo: iamkushs/fpl-intelligence
    token: $GITHUB_TOKEN
  required_labels:
    - symphony
  active_states:
    - open
  terminal_states:
    - closed
polling:
  interval_ms: 30000
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    ./scripts/agent-bootstrap.sh
    ./scripts/verify-agent-runtime.sh --allow-missing-github-auth
  after_create_windows:
    - .\scripts\agent-bootstrap.ps1
    - .\scripts\verify-agent-runtime.ps1 -AllowMissingGitHubAuth
  timeout_ms: 600000
agent:
  max_concurrent_agents: 2
  max_turns: 20
codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000
observability:
  dashboard_enabled: true
  refresh_ms: 1000
server:
  host: 127.0.0.1
  port: 4000
---

You are implementing GitHub issue `{{ issue.identifier }}` in an isolated workspace for `iamkushs/fpl-intelligence`.

This is unattended execution. Do not wait for interactive approval for normal repository work. Work only inside the issue workspace, never expose production credentials, never auto-merge, and stop early only for a true external blocker.

The issue title and body are the task; the issue body is the canonical acceptance criteria. Implement only this issue. Inspect and reuse current code before adding abstractions, and do not create competing subsystems. Add or update tests for changed behavior.

Do not inspect legacy/local Markdown files for requirements. The only Markdown policy or requirement sources you may rely on are root `WORKFLOW.md`, root `AGENTS.md`, and the GitHub issue body. Later evaluation issues will include or explicitly provide their canonical research methodology and prompts in their issue bodies.

Research and application state belong in PostgreSQL/database persistence, not generated Markdown files. Workpads are GitHub issue comments, never repository files. Temporary output must remain ignored and uncommitted.

## Durable GitHub state

- `symphony`: eligible for autonomous implementation.
- `symphony-review`: implementation and verification are complete; the open issue awaits human review and must not have `symphony`.
- `symphony-blocked`: a true external blocker prevents completion; the open issue must not have `symphony` and the blocker must be recorded in its workpad.

Do not close an implementation issue merely to stop polling. Do not rely on Symphony's in-memory blocked state.

## Start or resume

1. Inspect the current branch, `HEAD`, `git status`, existing changes and commits, and any existing PR.
2. Fetch `origin`. Understand whether the issue branch contains current `origin/main`; merge `origin/main` before implementation when synchronization is needed. Do not discard existing work.
3. Read the issue body and find the single active issue comment beginning `## Codex Workpad`. Create it only when none exists; thereafter edit that same comment instead of posting progress comments or changing the issue body.
4. Reconcile completed acceptance criteria and validation from Git state and the workpad. Resume from current state without repeating completed investigation or implementation unnecessarily.
5. Keep the workpad current using exactly this structure:

```text
## Codex Workpad

### Plan

### Acceptance Criteria

### Validation

### State / Progress

### Notes

### Blockers
```

Record the branch and short `HEAD`, synchronization results, meaningful milestones, validation commands/results, commit, and PR handoff in the workpad. Retries after an App Server timeout or stall follow this same reconciliation flow and must not destructively restart.

## Implementation and handoff

1. Implement only the current issue, keep the workpad checklist accurate, and fix code bugs, test failures, and merge conflicts rather than calling them external blockers.
2. Run targeted verification, then `./scripts/verify-all.sh` on Linux/CI or `.\scripts\verify-all.ps1` on Windows. Review the diff and fix task-caused failures without weakening checks.
3. Fetch `origin` and merge the latest `origin/main` into the issue branch. Resolve conflicts, rerun targeted checks and the platform verification gate, and record evidence in the workpad.
4. Prefer merge-based synchronization. Never use plain `--force`; use `--force-with-lease` only after an intentional, documented history rewrite. Do not use destructive Git recovery to mask authentication or permission errors.
5. Create or update a focused commit, push the issue branch, and create or update its GitHub PR. Do not auto-merge.
6. Confirm the final diff, PR, and validation evidence in the workpad. Remove `symphony` from the issue and add `symphony-review`, leaving the issue open for human review.

## True external blockers

Only missing required credentials, permissions, tools, or an unavailable required external service qualify after safe in-workspace alternatives are exhausted. Record what is missing, why it prevents acceptance, and the exact unblock action in `### Blockers`; then remove `symphony`, add `symphony-blocked`, and leave the issue open. Normal engineering difficulty is not a blocker.
