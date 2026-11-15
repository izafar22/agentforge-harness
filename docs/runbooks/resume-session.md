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

GitHub PR and workflow discovery paginate to completeness, within a 20-page safety bound; partial results, missing workflow total counts, malformed entries, and API failures are UNKNOWN. Workflow evidence is **SHA-scoped**, not specifically tied to the PR's required checks, and does not establish required check policy, approval status or branch protection. Do not interpret successful workflow runs as merge authorization. Redirects are disabled for GitHub API requests carrying tokens. The checkpoint's work branch must match the checkout; a missing baseline SHA is UNKNOWN. For minimally mutating inspection use `python -B` and `GIT_OPTIONAL_LOCKS=0`.
