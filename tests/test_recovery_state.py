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


    def test_resume_rejects_nonstr_next_action_values(self):
        project = verify.parse_simple_yaml((ROOT / "harness/project.yaml").read_text())
        for bad in (None, False, []):
            checkpoint = verify.parse_simple_yaml(
                (ROOT / "harness/checkpoint.yaml").read_text())
            checkpoint["next_action"]["description"] = bad
            with self.subTest(value=bad):
                with self.assertRaisesRegex(ValueError, "next_action.description"):
                    verify.validate(project, checkpoint)

    def test_unavailable_checkpoint_lookup_is_unknown_not_conflict(self):
        checkpoint = (ROOT / "harness/checkpoint.yaml").read_text()
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "harness/project.yaml").write_text(
                (ROOT / "harness/project.yaml").read_text())
            (root / "harness/checkpoint.yaml").write_text(
                checkpoint.replace("last_verified_commit: null",
                                   "last_verified_commit: " + SHA))
            def fake(root, *args):
                return {"symbolic-ref": "feature/phase1-memory-recovery",
                        "rev-parse": SHA, "status": ""}.get(args[0])
            with patch.object(verify, "git", side_effect=fake):
                state = verify.inspect(root)
        self.assertEqual(state["checkpoint_baseline_status"], "UNKNOWN")
        self.assertIsNone(state["checkpoint_commit_exists"])
        self.assertFalse(any("CONFLICT: checkpoint" in x for x in state["issues"]))


    def test_excessively_nested_checkpoint_rejected_before_output(self):
        project = verify.parse_simple_yaml((ROOT / "harness/project.yaml").read_text())
        checkpoint = verify.parse_simple_yaml((ROOT / "harness/checkpoint.yaml").read_text())
        level = checkpoint
        for index in range(1100):
            level["extra"] = {}
            level = level["extra"]
        with self.assertRaisesRegex(ValueError, "nesting or size limits"):
            verify.validate(project, checkpoint)

    def test_deep_checkpoint_verifier_cli_exits_invalid(self):
        from unittest.mock import patch
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "harness/project.yaml").write_text(
                (ROOT / "harness/project.yaml").read_text())
            checkpoint = (ROOT / "harness/checkpoint.yaml").read_text()
            checkpoint += "extra:\n" + "".join(("  " * n) + f"node{n}:\n"
                                                    for n in range(40))
            (root / "harness/checkpoint.yaml").write_text(checkpoint)
            with patch("sys.argv", ["verify_context.py", "--root", str(root), "--json"]):
                with redirect_stdout(io.StringIO()) as output:
                    code = verify.main()
        self.assertEqual(code, 2)
        self.assertIn("error", output.getvalue())


    def test_context_verifier_handles_symlink_loop(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.symlink_to(loop)
            with patch("sys.argv", ["verify_context.py", "--root", str(loop), "--json"]):
                with redirect_stdout(io.StringIO()) as output:
                    code = verify.main()
        self.assertEqual(code, 2)
        self.assertIn("error", output.getvalue())

    def test_resume_handles_symlink_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.symlink_to(loop)
            with patch("sys.argv", ["generate_resume.py", "--root", str(loop)]):
                with self.assertRaises(SystemExit) as outcome:
                    resume.main()
        self.assertEqual(outcome.exception.code, 2)

if __name__ == "__main__":
    unittest.main()
