#!/usr/bin/env python3
"""Read-only validation and local Git inspection of AgentForge memory."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def scalar(value, line):
    if value in ("null", "~"):
        return None
    if value == "[]":
        return []
    if value in ("true", "false"):
        return value == "true"
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        return value[1:-1]
    if value.startswith(("[", "{", "!")):
        raise ValueError(f"line {line}: unsupported YAML scalar")
    return value


def parse_simple_yaml(text):
    """Read only the restricted, two-space YAML subset used by this harness."""
    root = {}
    stack = [(-1, root)]
    lines = text.splitlines()
    for number, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent] or indent % 2:
            raise ValueError(f"line {number}: invalid indentation")
        item = raw.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if item.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"line {number}: list without parent")
            parent.append(scalar(item[2:], number))
            continue
        if not isinstance(parent, dict) or ":" not in item:
            raise ValueError(f"line {number}: unsupported YAML")
        key, val = item.split(":", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][\w-]*", key) or key in parent:
            raise ValueError(f"line {number}: invalid or duplicate key")
        val = val.strip()
        if val:
            parent[key] = scalar(val, number)
        else:
            following = next((x.strip() for x in lines[number:] if x.strip() and not x.lstrip().startswith("#")), "")
            parent[key] = [] if following.startswith("- ") else {}
            stack.append((indent, parent[key]))
    return root


def required(mapping, keys, label):
    if not isinstance(mapping, dict):
        raise ValueError(f"{label} must be a mapping")
    missing = set(keys) - set(mapping)
    if missing:
        raise ValueError(f"{label} missing: {', '.join(sorted(missing))}")


def validate(project, checkpoint):
    """Reject deeply nested or oversized input before JSON report serialization."""
    for label, value in (("project", project), ("checkpoint", checkpoint)):
        stack = [(value, 0)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if depth > 32 or count > 10000:
                raise ValueError(f"{label} exceeds supported nesting or size limits")
            if isinstance(node, dict):
                stack.extend((child, depth + 1) for child in node.values())
            elif isinstance(node, list):
                stack.extend((child, depth + 1) for child in node)

    required(project, ("schema_version", "project", "repository", "policies"), "project")
    required(checkpoint, ("schema_version", "task", "git", "progress", "blockers", "next_action", "verification"), "checkpoint")
    for name, obj in (("project", project), ("checkpoint", checkpoint)):
        if str(obj["schema_version"]) != "1":
            raise ValueError(f"{name}: unsupported schema version")
    required(project["project"], ("id", "name"), "project.project")
    required(project["repository"], ("provider", "owner", "name", "default_branch"), "project.repository")
    if project["repository"]["provider"] != "github":
        raise ValueError("only GitHub is supported")
    required(project["policies"], ("require_pr", "require_approval_for_merge", "require_approval_for_deploy"), "policies")
    if any(type(project["policies"][k]) is not bool for k in ("require_pr", "require_approval_for_merge", "require_approval_for_deploy")):
        raise ValueError("policies must be booleans")
    required(checkpoint["task"], ("id", "title", "status"), "task")
    if checkpoint["task"]["status"] not in ("not_started", "in_progress", "blocked", "completed"):
        raise ValueError("invalid task status")
    required(checkpoint["git"], ("base_branch", "work_branch", "last_verified_commit"), "git")
    for field in ("base_branch", "work_branch"):
        if not isinstance(checkpoint["git"][field], str) or not checkpoint["git"][field].strip():
            raise ValueError(f"git.{field} must be a nonempty string")
    baseline = checkpoint["git"]["last_verified_commit"]
    if baseline is not None and (not isinstance(baseline, str)
                                 or not re.fullmatch(r"[0-9a-fA-F]{7,40}", baseline)):
        raise ValueError("git.last_verified_commit must be null or a hex commit SHA")
    required(checkpoint["progress"], ("completed", "remaining"), "progress")
    for key in ("completed", "remaining"):
        if not isinstance(checkpoint["progress"][key], list) or not all(isinstance(x, str) for x in checkpoint["progress"][key]):
            raise ValueError("progress entries must be string lists")
    if not isinstance(checkpoint["blockers"], list):
        raise ValueError("blockers must be a list")
    required(checkpoint["next_action"], ("type", "description"), "next_action")
    for name in ("type", "description"):
        value = checkpoint["next_action"][name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"next_action.{name} must be a nonempty string")
    required(checkpoint["verification"], ("status", "verified_at"), "verification")
    if checkpoint["verification"]["status"] not in ("verified", "unverified", "conflict"):
        raise ValueError("invalid verification status")


def git(root, *arguments):
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True,
                                text=True, check=False, timeout=10,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def inspect(root):
    root = Path(root).resolve()
    project = parse_simple_yaml((root / "harness/project.yaml").read_text(encoding="utf-8"))
    checkpoint = parse_simple_yaml((root / "harness/checkpoint.yaml").read_text(encoding="utf-8"))
    validate(project, checkpoint)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    head = git(root, "rev-parse", "HEAD")
    dirty = git(root, "status", "--porcelain")
    issues = []
    if head is None:
        issues.append("Git HEAD unavailable")
    if dirty is None:
        issues.append("Cannot inspect working tree")
    elif dirty:
        issues.append("Working tree has uncommitted changes")
    expected_branch = checkpoint["git"]["work_branch"]
    branch_status = "UNKNOWN"
    if branch is None:
        issues.append("UNKNOWN: current branch unavailable")
    elif branch != expected_branch:
        branch_status = "CONFLICT"
        issues.append(f"CONFLICT: checkpoint work branch '{expected_branch}' differs from checkout '{branch}'")
    else:
        branch_status = "VERIFIED"
    claimed = checkpoint["git"]["last_verified_commit"]
    baseline_status = "UNKNOWN"
    if not claimed:
        issues.append("UNKNOWN: checkpoint has no last_verified_commit baseline")
    exists = None
    if claimed:
        object_type = git(root, "cat-file", "-t", claimed)
        exists = object_type == "commit" if object_type is not None else None
        if object_type is None:
            issues.append("UNKNOWN: cannot inspect checkpoint commit object")
        elif not exists:
            baseline_status = "CONFLICT"
            issues.append("CONFLICT: checkpoint SHA does not refer to a commit")
        elif head != claimed:
            baseline_status = "CONFLICT"
            issues.append("CONFLICT: checkpoint commit differs from HEAD")
        else:
            baseline_status = "VERIFIED"
    return {
        "project": project["project"]["id"],
        "repository": f'{project["repository"]["owner"]}/{project["repository"]["name"]}',
        "branch": branch, "head": head, "dirty": bool(dirty) if dirty is not None else None,
        "checkpoint_branch": expected_branch, "checkpoint_branch_status": branch_status,
        "checkpoint_baseline_status": baseline_status,
        "checkpoint_commit": claimed, "checkpoint_commit_exists": exists,
        "checkpoint": checkpoint, "issues": issues, "policies": project["policies"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect(args.root)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"INVALID: {exc}")
        return 2
    try:
        if args.json:
            print(json.dumps(report, indent=2, allow_nan=False))
        else:
            print(f'VALID CONTRACT: {report["repository"]}')
            print(f'Git: {report["branch"] or "unknown"} @ {report["head"] or "unknown"}')
            for issue in report["issues"]:
                print(f"WARNING: {issue}")
    except (TypeError, ValueError, RecursionError) as exc:
        print(json.dumps({"error": "Cannot serialize validated context"}) if args.json
              else "INVALID: Cannot serialize validated context")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
