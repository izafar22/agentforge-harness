#!/usr/bin/env python3
"""Produce a conservative, agent-ready resume report."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
from urllib import request, error

spec = importlib.util.spec_from_file_location("verify_context", Path(__file__).with_name("verify_context.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def github_get(repo, path, token):
    req = request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "agentforge-harness",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with request.urlopen(req, timeout=8) as response:
            return json.load(response), None
    except (error.URLError, TimeoutError, ValueError) as exc:
        return None, str(exc)


def build(root, github=False, fetcher=github_get):
    state = module.inspect(root)
    checkpoint = state["checkpoint"]
    issues = list(state["issues"])
    remote = "NOT REQUESTED"
    if github:
        if not state["head"]:
            data, err = None, "HEAD unavailable"
        else:
            data, err = fetcher(state["repository"], "commits/" + state["head"], os.getenv("GITHUB_TOKEN"))
        remote = "VERIFIED HEAD" if data and data.get("sha") == state["head"] else "UNKNOWN"
        if remote == "UNKNOWN":
            issues.append("GitHub HEAD verification unavailable or mismatched: " + (err or "mismatch"))
    readiness = "REQUIRES INVESTIGATION" if issues else "LOCAL STATE VERIFIED; PR/CI UNKNOWN"
    lines = [
        f'# Resume: {state["project"]}', "",
        f'**Task:** {checkpoint["task"]["id"]} — {checkpoint["task"]["title"]}',
        f'**Checkpoint status (claim):** {checkpoint["task"]["status"]}',
        f'**Local Git:** {state["branch"] or "unknown"} @ {state["head"] or "unknown"}',
        f"**Remote verification:** {remote}",
        f"**Readiness:** {readiness}", "", "## Reported completed work",
    ]
    lines.extend("- " + item for item in checkpoint["progress"]["completed"])
    lines.extend(["", "## Reported remaining work"])
    lines.extend("- " + item for item in checkpoint["progress"]["remaining"])
    lines.extend(["", "## Issues / unknowns"])
    lines.extend("- " + issue for issue in issues)
    if not issues:
        lines.append("- No local inconsistencies; PR/CI and progress claims remain unverified.")
    lines.extend(["", "## Next reported action", checkpoint["next_action"]["description"],
                  "", "## Guardrails", "- Verify PR/CI facts independently.",
                  "- Require explicit human approval before merge or deployment.",
                  "- Treat checkpoint progress as claims, not independent proof."])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--github", action="store_true")
    args = parser.parse_args()
    try:
        print(build(args.root, args.github))
    except (ValueError, OSError) as exc:
        print(f"INVALID: {exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
