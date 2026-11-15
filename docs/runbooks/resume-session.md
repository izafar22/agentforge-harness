# Fresh-session resume protocol

1. Read `AGENTS.md`, project manifest, checkpoint, architecture and relevant ADRs.
2. Confirm you are on the intended branch; do not switch automatically if the working tree is dirty.
3. Run `python scripts/verify_context.py --root . --json`.
4. Run `python scripts/generate_resume.py --root . --github` (requires GitHub network access; optional GITHUB_TOKEN).
5. Compare checkpoint claims with local HEAD, branch, dirty worktree and GitHub evidence.
6. Treat unavailable, incomplete, ambiguous or missing evidence as UNKNOWN, never as success.
7. Present scope, next action, intended tests, blockers and approval gates.
8. Implement in a task branch, validate, record progress and propose a PR. No unapproved merge or deployment.

## Limitations

GitHub PR discovery is first-page bounded. CI run verification checks workflow conclusions at the commit SHA, but does not establish required check policy, approval status or branch protection. Do not interpret a successful workflow as merge authorization.
