# Architecture

**Developer/architect** defines requirements and approves protected changes. **Codex or another coding agent** plans, writes code, executes tests and proposes PRs. **Repository harness** supplies durable architecture, instructions, checkpoints and resume procedure.

**Deterministic context verifier** validates the restricted memory contract, inspects HEAD, branch, worktree cleanliness and checkpoint commit. **Optional GitHub adapter** verifies immutable commit SHA, discovers matching PRs, and inspects SHA-level workflow runs with bounded, completeness-checked pagination. It does not verify required checks, branch protection, reviews, or merge authorization. Incomplete/malformed evidence remains UNKNOWN. Local verifier flags checkpoint branch drift as CONFLICT and a missing checkpoint SHA as UNKNOWN.

## Truth hierarchy
1. Verified Git and GitHub state for commits, branches, PRs and CI.
2. Version-controlled architecture docs and ADRs for design decisions.
3. Checkpoints for previously reported task progress (unverified).
4. Conversation history as optional background, never sole source of truth.

## Phase 1 boundary
No multi-agent coordination, orchestration runtime, CI claims, autonomous merging or deployment. Conflict or missing verification blocks risky actions but not read-only inspection.