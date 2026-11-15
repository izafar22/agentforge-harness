# ADR-001: Repository-based persistent context

Status: Accepted (initial design).

Use Markdown and a deliberately restricted YAML checkpoint to make project memory portable and reviewable. Use small Python tools to validate the contract and inspect Git; use the GitHub API only when explicitly requested.

Rationale: agent sessions are ephemeral, while repository context persists. Deterministic checks prevent hallucinated commit facts. Reuse mature coding-agent runtimes rather than building another model/agent framework.

Consequences: concurrent checkpoint edits need Git conflict resolution. Offline inspection cannot establish PR or CI status. Higher-scale orchestration can add a transactional state store later.