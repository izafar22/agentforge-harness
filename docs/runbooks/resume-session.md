# Fresh-session resume protocol

1. Read `AGENTS.md`, project manifest, checkpoint, architecture and relevant ADRs.
2. Run `python scripts/verify_context.py --root . --json`.
3. Run `python scripts/generate_resume.py --root .`; optionally add `--github`.
4. Compare checkpoint claims with local HEAD, branch, dirty worktree and any GitHub evidence.
5. Do not convert missing evidence into success. Investigate conflicts before editing.
6. Present scope, next action, intended tests, blockers and approval gates.
7. Implement in a task branch, validate, record progress and propose a PR. No unapproved merge or deployment.