# Architecture

**Developer/architect** defines requirements and approves protected changes. **Codex or another coding agent** plans, writes code, executes tests and proposes PRs. **Repository harness** supplies durable architecture, instructions, checkpoints and resume procedure.

**Deterministic context verifier** validates the restricted memory contract, inspects HEAD, branch, worktree cleanliness and checkpoint commit. **Optional GitHub adapter** checks the current commit by immutable SHA. It does not currently inspect PR reviews or CI status.

## Truth hierarchy
1. Verified Git and GitHub state for commits, branches, PRs and CI.
2. Version-controlled architecture docs and ADRs for design decisions.
3. Checkpoints for previously reported task progress (unverified).
4. Conversation history as optional background, never sole source of truth.

## Phase 1 boundary
No multi-agent coordination, orchestration runtime, CI claims, autonomous merging or deployment. Conflict or missing verification blocks risky actions but not read-only inspection.