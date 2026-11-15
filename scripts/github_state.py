"""Read-only GitHub reconciliation. Never equate API failures with successful checks."""
import json
import os
from urllib import error, parse, request


def api_get(repo, path, token=None):
    url = "https://api.github.com/repos/" + repo + "/" + path
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "agentforge-harness"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with request.urlopen(request.Request(url, headers=headers), timeout=10) as response:
            return json.load(response), None
    except (error.URLError, TimeoutError, ValueError) as exc:
        return None, str(exc)


def reconcile(repo, branch, head, token=None, fetch=api_get):
    """Only mark facts VERIFIED when API evidence is complete and unambiguous."""
    evidence = {"head": "UNKNOWN", "pull_requests": "UNKNOWN", "ci": "UNKNOWN",
                "pr": None, "ci_conclusion": None, "issues": []}
    if not head or not branch:
        evidence["issues"].append("Cannot reconcile GitHub without local HEAD and branch")
        return evidence
    commit, err = fetch(repo, "commits/" + parse.quote(head, safe=""), token)
    if not isinstance(commit, dict) or commit.get("sha") != head:
        evidence["issues"].append("Remote commit verification failed: " + (err or "SHA mismatch"))
        return evidence
    evidence["head"] = "VERIFIED"
    # Search for PRs from this repository's branch. Filter by SHA to avoid stale branch matches.
    owner = repo.split("/", 1)[0]
    query = parse.urlencode({"state": "all", "head": owner + ":" + branch, "per_page": 100})
    prs, err = fetch(repo, "pulls?" + query, token)
    if not isinstance(prs, list):
        evidence["issues"].append("PR verification unavailable: " + (err or "invalid response"))
    else:
        matching = [p for p in prs if isinstance(p, dict) and
                    p.get("head", {}).get("sha") == head]
        if len(matching) == 1:
            p = matching[0]
            evidence["pull_requests"] = "VERIFIED"
            evidence["pr"] = {"number": p.get("number"), "state": p.get("state"),
                              "draft": p.get("draft"), "merged_at": p.get("merged_at")}
        elif not matching:
            evidence["pull_requests"] = "NONE FOUND (first page only)"
            evidence["issues"].append("No PR at this HEAD found in first page; absence not exhaustive")
        else:
            evidence["pull_requests"] = "AMBIGUOUS"
            evidence["issues"].append("Multiple PRs match this branch and commit")
    runs, err = fetch(repo, "actions/runs?" + parse.urlencode({"head_sha": head, "per_page": 100}), token)
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        evidence["issues"].append("CI verification unavailable: " + (err or "invalid response"))
    else:
        candidates = [r for r in runs["workflow_runs"] if r.get("head_sha") == head]
        if not candidates:
            evidence["ci"] = "UNKNOWN"
            evidence["issues"].append("No CI runs found; not evidence of success")
        elif any(r.get("status") != "completed" for r in candidates):
            evidence["ci"] = "PENDING"
        elif all(r.get("conclusion") == "success" for r in candidates):
            evidence["ci"] = "VERIFIED"
            evidence["ci_conclusion"] = "success"
        else:
            evidence["ci"] = "FAILED OR INCOMPLETE"
            evidence["ci_conclusion"] = ",".join(sorted(set(str(r.get("conclusion")) for r in candidates)))
            evidence["issues"].append("At least one workflow did not succeed")
    return evidence
