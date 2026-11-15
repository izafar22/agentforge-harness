"""Conservative read-only GitHub evidence for project resume.

Successful SHA-level workflows never imply required checks or merge approval.
"""
import json
import re
from urllib import error, parse, request

PAGE_SIZE = 100
MAX_PAGES = 20


class NoRedirect(request.HTTPRedirectHandler):
    """Do not forward authorization headers to another URL."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_get(repo, path, token=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return None, "Invalid GitHub repository identifier"
    if not isinstance(path, str) or path.startswith(("/", "http:", "https:")) or ".." in path:
        return None, "Invalid GitHub API path"
    url = "https://api.github.com/repos/" + repo + "/" + path
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "agentforge-harness"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        opener = request.build_opener(NoRedirect())
        with opener.open(request.Request(url, headers=headers), timeout=10) as response:
            if response.geturl() != url:
                return None, "GitHub API origin changed"
            return json.load(response), None
    except (error.URLError, TimeoutError, ValueError) as exc:
        return None, "GitHub API request failed: " + type(exc).__name__


def _pages(repo, endpoint, token, fetch, collection):
    """Fetch all pages; never return partial evidence as verified."""
    items = []
    expected_total = None
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in endpoint else "?"
        path = endpoint + sep + parse.urlencode({"per_page": PAGE_SIZE, "page": page})
        payload, err = fetch(repo, path, token)
        if err:
            return None, f"GitHub {collection} page {page} unavailable: {err}"
        if collection == "pulls":
            batch = payload
        else:
            if not isinstance(payload, dict):
                return None, f"Malformed {collection} response on page {page}"
            total = payload.get("total_count")
            if type(total) is not int or total < 0:
                return None, f"Missing or invalid {collection} total_count on page {page}"
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                return None, f"{collection} total_count changed during pagination"
            batch = payload.get("workflow_runs")
        if not isinstance(batch, list) or any(not isinstance(x, dict) for x in batch):
            return None, f"Malformed {collection} entries on page {page}"
        if len(batch) > PAGE_SIZE:
            return None, f"Oversized {collection} page {page}"
        if collection == "runs":
            # Only unique, positive workflow run IDs establish complete evidence.
            ids = [r.get("id") for r in batch]
            if any(type(i) is not int or i <= 0 for i in ids):
                return None, f"Malformed or missing workflow run ID on page {page}"
            previous = {r["id"] for r in items}
            if len(set(ids)) != len(ids) or any(i in previous for i in ids):
                return None, f"Duplicate workflow run ID on page {page}; completeness unverified"
        items.extend(batch)
        if collection == "pulls":
            if len(batch) < PAGE_SIZE:
                return items, None
        else:
            if len(items) == expected_total:
                return items, None
            if len(items) > expected_total or len(batch) < PAGE_SIZE:
                return None, f"Incomplete {collection} pagination: {len(items)} of {expected_total}"
    return None, f"{collection} results exceeded {MAX_PAGES} pages; completeness unverified"


def reconcile(repo, branch, head, token=None, fetch=api_get):
    """Reconcile commit, PRs, and SHA-scoped CI, failing closed on incomplete data."""
    evidence = {"head": "UNKNOWN", "pull_requests": "UNKNOWN", "ci": "UNKNOWN",
                "pr": None, "ci_conclusion": None,
                "ci_scope": "SHA-level workflow runs; not required checks",
                "issues": []}
    if not head or not branch:
        evidence["issues"].append("Cannot reconcile GitHub without local HEAD and branch")
        return evidence
    commit, err = fetch(repo, "commits/" + parse.quote(head, safe=""), token)
    if err or not isinstance(commit, dict) or commit.get("sha") != head:
        evidence["issues"].append("Remote commit verification unavailable or mismatched")
        return evidence
    evidence["head"] = "VERIFIED"
    owner = repo.split("/", 1)[0]
    query = "pulls?" + parse.urlencode({"state": "all", "head": owner + ":" + branch})
    prs, err = _pages(repo, query, token, fetch, "pulls")
    if err:
        evidence["issues"].append("PR verification UNKNOWN: " + err)
    elif any(not isinstance(p.get("head"), dict) or not isinstance(p["head"].get("sha"), str)
             for p in prs):
        evidence["issues"].append("PR verification UNKNOWN: malformed head metadata")
    else:
        matches = [p for p in prs if p["head"]["sha"] == head]
        if len(matches) == 1:
            p = matches[0]
            if (type(p.get("number")) is not int or p["number"] <= 0
                or p.get("state") not in ("open", "closed")
                or type(p.get("draft")) is not bool
                or (p.get("merged_at") is not None and
                    (not isinstance(p["merged_at"], str) or not p["merged_at"].strip()))):
                evidence["issues"].append("PR verification UNKNOWN: invalid matching PR fields")
            else:
                evidence["pull_requests"] = "VERIFIED"
                evidence["pr"] = {"number": p["number"], "state": p["state"],
                                  "draft": p["draft"], "merged_at": p.get("merged_at")}
        elif not matches:
            evidence["pull_requests"] = "NONE FOUND"
            evidence["issues"].append("No PR at this SHA found in complete branch result set")
        else:
            evidence["pull_requests"] = "AMBIGUOUS"
            evidence["issues"].append("Multiple PRs match this branch and commit")
    runs, err = _pages(repo, "actions/runs?" + parse.urlencode({"head_sha": head}), token, fetch, "runs")
    if err:
        evidence["issues"].append("CI verification UNKNOWN: " + err)
    elif any(
        r.get("head_sha") != head
        or r.get("status") not in ("queued", "in_progress", "completed", "waiting",
                                  "requested", "pending")
        or (r["status"] == "completed" and
            (not isinstance(r.get("conclusion"), str)
             or r["conclusion"] not in ("success", "failure", "cancelled",
                                       "skipped", "timed_out", "action_required",
                                       "neutral", "stale")))
        or (r["status"] != "completed" and r.get("conclusion") is not None)
        for r in runs):
        evidence["issues"].append("CI verification UNKNOWN: malformed workflow entry or incorrect SHA")
    elif not runs:
        evidence["issues"].append("No workflow runs found; not evidence of success")
    elif any(r["status"] != "completed" for r in runs):
        evidence["ci"] = "PENDING"
    elif all(r["conclusion"] == "success" for r in runs):
        evidence["ci"] = "VERIFIED"
        evidence["ci_conclusion"] = "success"
    else:
        evidence["ci"] = "FAILED OR INCOMPLETE"
        evidence["ci_conclusion"] = ",".join(sorted(set(r["conclusion"] for r in runs)))
        evidence["issues"].append("At least one SHA-level workflow did not succeed")
    return evidence
