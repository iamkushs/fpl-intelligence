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
  max_concurrent_agents: 1
  max_turns: 20
recovery:
  max_incident_repairs: 3
  max_issue_repairs: 9
  reviewer_routes:
    - "5.5"
    - terra
    - sol
  rescue_timeout_ms: 900000
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

Queue dependencies use one exact issue-body line: `Depends-On: #10` or `Depends-On: #10, #11`.
References are same-repository issue numbers and all must be closed before the issue is eligible. A predecessor
PR is not sufficient. Malformed, duplicate, self-referential, and cyclic declarations fail visibly and safely.

## Start or resume

1. Inspect the current branch, `HEAD`, `git status`, existing changes and commits, and any existing PR using read-only Git commands. The host runner owns operations that write `.git`.
2. Reconcile the visible branch and workspace state. Do not fetch, switch, merge, stage, commit, or push: request host handoff after the implementation is verified, and the runner will synchronize with `origin/main` without discarding existing work.
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

Record the branch and short `HEAD`, meaningful milestones, validation commands/results, and handoff readiness in the workpad. The host runner records final synchronization, commit, push, and PR evidence. Retries after an App Server timeout or stall follow this same reconciliation flow and must not destructively restart.

## Implementation and handoff

1. Implement only the current issue, keep the workpad checklist accurate, and fix code bugs, test failures, and merge conflicts rather than calling them external blockers.
2. Run targeted verification, then `./scripts/verify-all.sh` on Linux/CI or `.\scripts\verify-all.ps1` on Windows. Review the diff and fix task-caused failures without weakening checks.
3. After verification passes, record the exact commands and successful results under `### Validation`, review the logical workspace diff, and add the literal marker `HOST HANDOFF READY`. Do not add it when verification is incomplete or failing.
4. The host runner inspects every changed/untracked path, enforces repository hygiene and Markdown policy, fetches and merge-synchronizes latest `origin/main`, and verifies the resulting effective tree. If synchronization changes the tree, that merged result must pass verification before it can be pushed.
5. The host runner stages only the inspected task changes, creates a focused commit, pushes the issue branch, and creates or updates the GitHub PR. Repository-local rerere remains enabled. Neither Codex nor the runner auto-merges.
6. The host runner records final synchronization, commit, push, PR, and validation evidence in the workpad, then removes `symphony` and adds `symphony-review`, leaving the issue open for human review.

## True external blockers

Only missing required credentials, permissions, tools, or an unavailable required external service qualify after safe in-workspace alternatives are exhausted. Record what is missing, why it prevents acceptance, and the exact unblock action in `### Blockers`; then remove `symphony`, add `symphony-blocked`, and leave the issue open. Normal engineering difficulty is not a blocker.
