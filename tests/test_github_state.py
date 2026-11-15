"""Mocked GitHub reconciliation; no network required."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("github_state", Path(__file__).resolve().parents[1] / "scripts/github_state.py")
gh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gh)
SHA = "a" * 40


def fake(repo, path, token):
    if path.startswith("commits/"):
        return {"sha": SHA}, None
    if path.startswith("pulls?"):
        return [{"number": 1, "state": "open", "draft": False, "merged_at": None,
                 "head": {"sha": SHA}}], None
    if path.startswith("actions/runs?"):
        return {"total_count": 1, "workflow_runs": [{"id": 1, "head_sha": SHA, "status": "completed", "conclusion": "success"}]}, None
    raise AssertionError(path)


class GitHubReconciliationTests(unittest.TestCase):
    def test_verified_state(self):
        report = gh.reconcile("example/repo", "feature/test", SHA, fetch=fake)
        self.assertEqual(report["head"], "VERIFIED")
        self.assertEqual(report["pr"]["number"], 1)
        self.assertEqual(report["ci_conclusion"], "success")

    def test_network_error_is_unknown(self):
        report = gh.reconcile("example/repo", "feature/test", SHA,
                              fetch=lambda *args: (None, "offline"))
        self.assertEqual(report["head"], "UNKNOWN")
        self.assertNotEqual(report["ci"], "VERIFIED")

    def test_failed_ci_not_success(self):
        def failing(repo, path, token):
            value, err = fake(repo, path, token)
            if path.startswith("actions/runs?"):
                value["workflow_runs"][0]["conclusion"] = "failure"
            return value, err
        report = gh.reconcile("example/repo", "feature/test", SHA, fetch=failing)
        self.assertEqual(report["ci"], "FAILED OR INCOMPLETE")

    def test_missing_pr_not_claimed_exhaustive(self):
        def missing(repo, path, token):
            if path.startswith("pulls?"):
                return [], None
            return fake(repo, path, token)
        report = gh.reconcile("example/repo", "feature/test", SHA, fetch=missing)
        self.assertEqual(report["pull_requests"], "NONE FOUND")

    def test_stale_pr_head_ignored(self):
        def stale(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": "b" * 40}}], None
            return fake(repo, path, token)
        report = gh.reconcile("example/repo", "feature/test", SHA, fetch=stale)
        self.assertIsNone(report["pr"])


    def test_truncated_workflow_page_unknown(self):
        run = {"id": 1, "head_sha": SHA, "status": "completed", "conclusion": "success"}
        def truncated(repo, path, token):
            if path.startswith("actions/runs?"):
                if "&page=1" in path:
                    return {"total_count": 101, "workflow_runs": [dict(run, id=i) for i in range(1, 101)]}, None
                return None, "offline"
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=truncated)
        self.assertEqual(result["ci"], "UNKNOWN")
        self.assertTrue(any("page 2" in i for i in result["issues"]))

    def test_failure_on_second_workflow_page(self):
        run = {"head_sha": SHA, "status": "completed", "conclusion": "success"}
        def second(repo, path, token):
            if path.startswith("actions/runs?"):
                return ({"total_count": 101, "workflow_runs": [dict(run, id=i) for i in range(1, 101)]} if "&page=1" in path
                        else {"total_count": 101, "workflow_runs": [dict(run, id=101, conclusion="failure")]}), None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=second)
        self.assertEqual(result["ci"], "FAILED OR INCOMPLETE")

    def test_second_pr_page_ambiguity(self):
        p = {"head": {"sha": SHA}, "number": 1, "draft": False, "state": "open"}
        other = {"head": {"sha": "b" * 40}}
        def second(repo, path, token):
            if path.startswith("pulls?"):
                return ([p] + [other] * 99 if "&page=1" in path else [dict(p, number=2)]), None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=second)
        self.assertEqual(result["pull_requests"], "AMBIGUOUS")

    def test_incomplete_pr_page_unknown(self):
        p = {"head": {"sha": SHA}, "number": 1, "draft": False, "state": "open"}
        def truncated(repo, path, token):
            if path.startswith("pulls?"):
                return ([p] * 100, None) if "&page=1" in path else (None, "offline")
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=truncated)
        self.assertEqual(result["pull_requests"], "UNKNOWN")

    def test_null_pr_head_unknown_not_crash(self):
        def malformed(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": None}], None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=malformed)
        self.assertEqual(result["pull_requests"], "UNKNOWN")

    def test_null_workflow_entry_unknown_not_crash(self):
        def malformed(repo, path, token):
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [None]}, None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=malformed)
        self.assertEqual(result["ci"], "UNKNOWN")

    def test_queued_workflow_pending(self):
        def queued(repo, path, token):
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [
                    {"id": 1, "head_sha": SHA, "status": "queued", "conclusion": None}]}, None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=queued)
        self.assertEqual(result["ci"], "PENDING")

    def test_partial_endpoint_failure_is_unknown(self):
        def unavailable(repo, path, token):
            if path.startswith("pulls?"):
                return None, "permissions"
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=unavailable)
        self.assertEqual(result["pull_requests"], "UNKNOWN")
        self.assertEqual(result["ci"], "VERIFIED")

    def test_missing_total_count_unknown(self):
        def malformed(repo, path, token):
            if path.startswith("actions/runs?"):
                return {"workflow_runs": [
                    {"id": 1, "head_sha": SHA, "status": "completed", "conclusion": "success"}]}, None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=malformed)
        self.assertEqual(result["ci"], "UNKNOWN")

    def test_authenticated_redirect_not_followed(self):
        self.assertIsNone(gh.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://other.example"))


    def test_duplicate_workflow_ids_across_pages_unknown(self):
        def duplicate(repo, path, token):
            if path.startswith("actions/runs?"):
                if "&page=1" in path:
                    return {"total_count": 101, "workflow_runs": [
                        {"id": i, "head_sha": SHA, "status": "completed",
                         "conclusion": "success"} for i in range(1, 101)]}, None
                return {"total_count": 101, "workflow_runs": [
                    {"id": 100, "head_sha": SHA, "status": "completed",
                     "conclusion": "success"}]}, None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=duplicate)
        self.assertEqual(result["ci"], "UNKNOWN")
        self.assertTrue(any("Duplicate workflow run ID" in i for i in result["issues"]))

    def test_invalid_workflow_status_unknown(self):
        def invalid(repo, path, token):
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [
                    {"id": 12, "head_sha": SHA, "status": "rubbish",
                     "conclusion": None}]}, None
            return fake(repo, path, token)
        self.assertEqual(gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["ci"], "UNKNOWN")

    def test_empty_completed_conclusion_unknown(self):
        def invalid(repo, path, token):
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [
                    {"id": 12, "head_sha": SHA, "status": "completed",
                     "conclusion": ""}]}, None
            return fake(repo, path, token)
        self.assertEqual(gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["ci"], "UNKNOWN")

    def test_invalid_pr_number_and_merged_date_unknown(self):
        for invalid_field in ({"number": 0}, {"merged_at": ["bad"]}):
            def invalid(repo, path, token):
                if path.startswith("pulls?"):
                    p = {"head": {"sha": SHA}, "number": 1, "state": "open",
                         "draft": False, "merged_at": None}
                    p.update(invalid_field)
                    return [p], None
                return fake(repo, path, token)
            with self.subTest(invalid_field=invalid_field):
                self.assertEqual(
                    gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["pull_requests"],
                    "UNKNOWN")

if __name__ == "__main__":
    unittest.main()
