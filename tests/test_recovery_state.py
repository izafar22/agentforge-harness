"""Regression tests: branch drift, unknown baseline, and evidence-aware resume."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("verify_context")
resume = load("generate_resume")
SHA = "a" * 40


def fake_git(root, *args):
    if args[0] == "symbolic-ref":
        return "feature/harness-002-github-reconciliation"
    if args[0] == "rev-parse":
        return SHA
    if args[0] == "status":
        return ""
    return None


class RecoveryStateTests(unittest.TestCase):
    def test_checkpoint_branch_mismatch_detected(self):
        with patch.object(verify, "git", side_effect=fake_git):
            result = verify.inspect(ROOT)
        self.assertEqual(result["checkpoint_branch_status"], "CONFLICT")
        self.assertTrue(any("checkpoint work branch" in issue for issue in result["issues"]))

    def test_null_baseline_is_unknown(self):
        with patch.object(verify, "git", side_effect=fake_git):
            result = verify.inspect(ROOT)
        self.assertEqual(result["checkpoint_baseline_status"], "UNKNOWN")
        self.assertTrue(any("no last_verified_commit" in issue for issue in result["issues"]))

    def test_resume_reports_remote_evidence_and_conflict(self):
        def fake(repo, path, token):
            if path.startswith("commits/"):
                return {"sha": SHA}, None
            if path.startswith("pulls?"):
                return [{"number": 2, "draft": True, "state": "open",
                         "head": {"sha": SHA}}], None
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [
                    {"head_sha": SHA, "status": "completed", "conclusion": "success"}]}, None
            raise AssertionError(path)
        with patch.object(resume.module, "git", side_effect=fake_git):
            output = resume.build(ROOT, github=True, fetcher=fake)
        self.assertIn("CONFLICT", output)
        self.assertIn("CI: VERIFIED", output)
        self.assertNotIn("PR/CI UNKNOWN", output)


if __name__ == "__main__":
    unittest.main()
