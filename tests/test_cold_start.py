"""Cold-start and deliberately inconsistent-memory tests."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("verify_context")
resume = load("generate_resume")


class ColdStartTests(unittest.TestCase):
    def setup_repo(self, checkpoint=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "harness").mkdir()
        (root / "harness/project.yaml").write_text((ROOT / "harness/project.yaml").read_text())
        (root / "harness/checkpoint.yaml").write_text(
            checkpoint if checkpoint is not None else (ROOT / "harness/checkpoint.yaml").read_text())
        return temp, root

    def test_valid_contract(self):
        project = verify.parse_simple_yaml((ROOT / "harness/project.yaml").read_text())
        checkpoint = verify.parse_simple_yaml((ROOT / "harness/checkpoint.yaml").read_text())
        verify.validate(project, checkpoint)

    def test_fresh_session_recovers_task_without_chat(self):
        temp, root = self.setup_repo()
        try:
            result = resume.build(root)
            self.assertIn("HARNESS-001", result)
            self.assertIn("cold-start", result)
            self.assertIn("PARTIAL — CHECKPOINT OR LOCAL EVIDENCE UNKNOWN", result)
        finally:
            temp.cleanup()

    def test_nonexistent_checkpoint_commit_flagged(self):
        checkpoint = (ROOT / "harness/checkpoint.yaml").read_text().replace(
            "last_verified_commit: null", "last_verified_commit: deadbeef")
        temp, root = self.setup_repo(checkpoint)
        try:
            state = verify.inspect(root)
            self.assertFalse(state["checkpoint_commit_exists"])
            self.assertTrue(any("not found locally" in message for message in state["issues"]))
        finally:
            temp.cleanup()

    def test_duplicate_yaml_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify.parse_simple_yaml("schema_version: 1\nschema_version: 1\n")

    def test_invalid_status_rejected(self):
        project = verify.parse_simple_yaml((ROOT / "harness/project.yaml").read_text())
        checkpoint = verify.parse_simple_yaml((ROOT / "harness/checkpoint.yaml").read_text())
        checkpoint["task"]["status"] = "imaginary"
        with self.assertRaisesRegex(ValueError, "invalid task status"):
            verify.validate(project, checkpoint)

    def test_github_unavailable_does_not_claim_success(self):
        temp, root = self.setup_repo()
        try:
            result = resume.build(root, github=True, fetcher=lambda repo, path, token: (None, "offline"))
            self.assertIn("Remote verification:** UNKNOWN", result)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
