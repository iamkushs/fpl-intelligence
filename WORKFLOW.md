---
tracker:
  kind: github
  api_key: $GITHUB_TOKEN
  repository: iamkushs/fpl-intelligence
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
  timeout_ms: 600000
agent:
  max_concurrent_agents: 2
  max_turns: 20
codex:
  command: codex app-server
  turn_timeout_ms: 3600000
---

You are implementing one GitHub issue in an isolated workspace for `iamkushs/fpl-intelligence`.

Read the issue title and body and treat the body as the acceptance criteria. Inspect the current repository code before modifying anything. Reuse the existing architecture and do not create competing subsystems. Implement only the current issue; do not anticipate future issues.

Add or update tests for changed behavior. Run `./scripts/verify-all.sh`, inspect your own diff, and fix failures caused by the task. Do not weaken checks. Research and application state belong in PostgreSQL/database persistence, not generated Markdown files; temporary local output must remain ignored and uncommitted.

Do not inspect legacy/local Markdown files for requirements. The only Markdown policy or requirement sources you may rely on are root `WORKFLOW.md`, root `AGENTS.md`, and the GitHub issue body. Later evaluation issues will include or explicitly provide their canonical research methodology and prompts in their issue bodies.

When complete, create a focused commit, push the issue branch, create or update a pull request when supported, and report concrete implementation and verification results back to the issue.
