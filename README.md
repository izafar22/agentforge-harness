# AgentForge Harness

A reusable **harness-engineering** foundation for supervised AI software development. Phase 1 implements project memory, deterministic local Git verification, conservative recovery reporting, and cold-start tests. It **does not** implement a new AI model runtime.

## Run

```bash
python scripts/verify_context.py --root . --json
python scripts/generate_resume.py --root .
python -m unittest discover -s tests -v
```

No third-party Python dependencies are required for offline inspection. The optional `--github` flag on the resume script checks the current commit against the GitHub API. PR and CI verification are **not yet implemented** and always remain unknown.

## Adopt in another repository

Copy `AGENTS.md`, `harness/`, `docs/`, and `scripts/` into the target repository, then update `harness/project.yaml`. A fresh Codex session should read `AGENTS.md`, run context verification and generate a resume plan before modifying anything.

## Safety

Git and GitHub are authoritative for Git state; ADRs are authoritative for design intent; checkpoints are only claims until reconciled. Human approvals are required for merges and production deployments. Set up actual GitHub branch and environment protections; prompts cannot enforce access controls.

See `docs/architecture.md` and `docs/runbooks/resume-session.md`.