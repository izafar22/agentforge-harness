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

    def test_dirty_worktree_prevents_verified_readiness(self):
        def dirty(root, *args):
            if args[0] == "status":
                return " M src/file.py"
            if args[0] == "cat-file":
                return "commit"
            return {"symbolic-ref": "feature/phase1-memory-recovery",
                    "rev-parse": SHA}.get(args[0])
        with patch.object(resume.module, "git", side_effect=dirty):
            with patch.object(resume.module, "inspect", wraps=resume.module.inspect):
                with patch.object(resume.module, "parse_simple_yaml", wraps=resume.module.parse_simple_yaml):
                    output = resume.build(ROOT)
        self.assertIn("PARTIAL", output)
        self.assertNotIn("GIT/GITHUB FACTS VERIFIED", output)

    def test_unreadable_worktree_prevents_verified_readiness(self):
        def unreadable(root, *args):
            if args[0] == "status":
                return None
            if args[0] == "cat-file":
                return "commit"
            return {"symbolic-ref": "feature/phase1-memory-recovery",
                    "rev-parse": SHA}.get(args[0])
        with patch.object(resume.module, "git", side_effect=unreadable):
            output = resume.build(ROOT)
        self.assertIn("PARTIAL", output)
        self.assertIn("Cannot inspect working tree", output)

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
                         "head": {"sha": SHA, "ref": "feature/test", "repo": {"full_name": "example/repo"}}}], None
            if path.startswith("actions/runs?"):
                return {"total_count": 1, "workflow_runs": [
                    {"id": 1, "head_sha": SHA, "status": "completed", "conclusion": "success"}]}, None
            raise AssertionError(path)
        with patch.object(resume.module, "git", side_effect=fake_git):
            output = resume.build(ROOT, github=True, fetcher=fake)
        self.assertIn("CONFLICT", output)
        self.assertIn("CI: VERIFIED", output)
        self.assertNotIn("PR/CI UNKNOWN", output)



    def test_boolean_checkpoint_commit_is_invalid(self):
        import copy
        project = verify.parse_simple_yaml((ROOT / "harness/project.yaml").read_text())
        checkpoint = verify.parse_simple_yaml((ROOT / "harness/checkpoint.yaml").read_text())
        checkpoint["git"]["last_verified_commit"] = True
        with self.assertRaisesRegex(ValueError, "git.last_verified_commit"):
            verify.validate(project, checkpoint)

    def test_git_status_timeout_is_reported_unknown(self):
        import subprocess
        from unittest.mock import patch
        original = verify.subprocess.run

        def timed(*args, **kwargs):
            if "status" in args[0]:
                raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 10))
            return original(*args, **kwargs)

        with patch.object(verify.subprocess, "run", side_effect=timed):
            result = verify.inspect(ROOT)
        self.assertIsNone(result["dirty"])
        self.assertIn("Cannot inspect working tree", result["issues"])

    def test_resume_cli_handles_invalid_checkpoint_without_traceback(self):
        from unittest.mock import patch
        with patch.object(resume.module, "inspect",
                          side_effect=ValueError("git.last_verified_commit invalid")):
            with patch("sys.argv", ["generate_resume.py", "--root", str(ROOT)]):
                with self.assertRaises(SystemExit) as outcome:
                    resume.main()
        self.assertEqual(outcome.exception.code, 2)

if __name__ == "__main__":
    unittest.main()
