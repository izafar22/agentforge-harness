"""Conservative read-only GitHub evidence for project resume.

Successful SHA-level workflows never imply required checks or merge approval.
"""
import json
import re
from datetime import datetime
from http.client import HTTPException
from urllib import error, parse, request

def reject_duplicate_json_keys(pairs):
    """JSON object keys must be unique; last-key-wins would forge evidence."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class GitHubObject(dict):
    """A decoded object retaining the HTTP Link header."""
    github_link = ""


class GitHubArray(list):
    """A decoded array retaining the HTTP Link header."""
    github_link = ""


def reject_non_json_constant(value):
    raise ValueError("Nonstandard JSON constant: " + value)


def next_link_present(header):
    """Return True/False, or None if the Link header is malformed."""
    if not isinstance(header, str):
        return None
    if not header:
        return False
    next_found = False
    for entry in header.split(","):
        match = re.fullmatch(r'\s*<https://api\.github\.com/[^>]+>\s*;\s*rel="(next|prev|first|last)"\s*', entry)
        if match is None:
            return None
        if match.group(1) == "next":
            if next_found:
                return None
            next_found = True
    return next_found


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
            payload = json.load(response, object_pairs_hook=reject_duplicate_json_keys,
                                parse_constant=reject_non_json_constant)
            # Retain Link metadata without changing the existing (payload, error) API.
            if isinstance(payload, dict):
                payload = GitHubObject(payload)
            elif isinstance(payload, list):
                payload = GitHubArray(payload)
            if isinstance(payload, (GitHubObject, GitHubArray)):
                payload.github_link = response.headers.get("Link", "") if hasattr(response, "headers") else ""
            return payload, None
    except (error.URLError, TimeoutError, ValueError, OSError, HTTPException, RecursionError) as exc:
        return None, "GitHub API request failed: " + type(exc).__name__


def _pages(repo, endpoint, token, fetch, collection):
    """Fetch all pages; never return partial evidence as verified."""
    items = []
    expected_total = None
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in endpoint else "?"
        path = endpoint + sep + parse.urlencode({"per_page": PAGE_SIZE, "page": page})
        try:
            payload, err = fetch(repo, path, token)
        except (error.URLError, TimeoutError, ValueError, OSError, HTTPException, RecursionError) as exc:
            return None, f"GitHub {collection} page {page} interrupted: {type(exc).__name__}"
        if err:
            return None, f"GitHub {collection} page {page} unavailable: {err}"
        has_next = next_link_present(getattr(payload, "github_link", ""))
        if has_next is None:
            return None, f"Malformed {collection} Link header on page {page}"
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
        if has_next:
            if collection == "runs" and len(items) >= expected_total:
                return None, f"Contradictory {collection} Link continuation and total_count"
            continue
        if collection == "pulls":
            if len(batch) < PAGE_SIZE:
                return items, None
        else:
            if len(items) == expected_total:
                return items, None
            if len(items) > expected_total or len(batch) < PAGE_SIZE:
                return None, f"Incomplete {collection} pagination: {len(items)} of {expected_total}"
    return None, f"{collection} results exceeded {MAX_PAGES} pages; completeness unverified"

def valid_github_timestamp(value):
    """GitHub merged_at must be null or a valid UTC RFC3339 timestamp."""
    if value is None:
        return True
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def valid_pr_head(pr, repo, branch):
    """An exact SHA alone is insufficient to establish branch provenance."""
    source = pr.get("head")
    if not isinstance(source, dict):
        return False
    source_repo = source.get("repo")
    return (source.get("ref") == branch
            and isinstance(source_repo, dict)
            and source_repo.get("full_name") == repo
            and isinstance(source.get("sha"), str)
            and re.fullmatch(r"[0-9a-fA-F]{40}", source["sha"]) is not None)


def valid_pr_record(pr, repo, branch):
    """Validate every returned PR, not only the record matching HEAD."""
    if not valid_pr_head(pr, repo, branch):
        return False
    if (type(pr.get("number")) is not int or pr["number"] <= 0
        or pr.get("state") not in ("open", "closed")
        or type(pr.get("draft")) is not bool
        or "merged_at" not in pr
        or not valid_github_timestamp(pr["merged_at"])):
        return False
    merged_at = pr["merged_at"]
    if (pr["state"] == "open" and merged_at is not None) or (merged_at and pr["draft"]):
        return False
    if "merged" in pr and (type(pr["merged"]) is not bool
                           or pr["merged"] != (merged_at is not None)):
        return False
    return True


def reconcile(repo, branch, head, token=None, fetch=api_get):
    """Reconcile commit, PRs, and SHA-scoped CI, failing closed on incomplete data."""
    evidence = {"head": "UNKNOWN", "pull_requests": "UNKNOWN", "ci": "UNKNOWN",
                "pr": None, "ci_conclusion": None,
                "ci_scope": "SHA-level workflow runs; not required checks",
                "issues": []}
    if not head or not branch:
        evidence["issues"].append("Cannot reconcile GitHub without local HEAD and branch")
        return evidence
    try:
        commit, err = fetch(repo, "commits/" + parse.quote(head, safe=""), token)
    except (error.URLError, TimeoutError, ValueError, OSError, HTTPException, RecursionError) as exc:
        commit, err = None, type(exc).__name__
    if err or not isinstance(commit, dict) or commit.get("sha") != head:
        evidence["issues"].append("Remote commit verification unavailable or mismatched")
        return evidence
    evidence["head"] = "VERIFIED"
    owner = repo.split("/", 1)[0]
    query = "pulls?" + parse.urlencode({"state": "all", "head": owner + ":" + branch})
    prs, err = _pages(repo, query, token, fetch, "pulls")
    if err:
        evidence["issues"].append("PR verification UNKNOWN: " + err)
    elif any(not valid_pr_record(p, repo, branch) for p in prs):
        evidence["issues"].append("PR verification UNKNOWN: malformed or contradictory PR entry")
    elif len({p["number"] for p in prs}) != len(prs):
        evidence["issues"].append("PR verification UNKNOWN: duplicate PR number in response")
    else:
        matches = [p for p in prs if p["head"]["sha"] == head]
        if len(matches) == 1:
            p = matches[0]
            evidence["pull_requests"] = "VERIFIED"
            evidence["pr"] = {"number": p["number"], "state": p["state"],
                              "draft": p["draft"], "merged_at": p["merged_at"]}
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
