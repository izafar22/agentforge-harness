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
        return {"workflow_runs": [{"head_sha": SHA, "status": "completed", "conclusion": "success"}]}, None
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
        self.assertIn("first page only", report["pull_requests"])

    def test_stale_pr_head_ignored(self):
        def stale(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": "b" * 40}}], None
            return fake(repo, path, token)
        report = gh.reconcile("example/repo", "feature/test", SHA, fetch=stale)
        self.assertIsNone(report["pr"])


if __name__ == "__main__":
    unittest.main()
