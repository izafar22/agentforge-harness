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
                 "head": {"sha": SHA, "ref": "feature/test", "repo": {"full_name": "example/repo"}}}], None
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
        p = {"head": {"sha": SHA, "ref": "feature/test", "repo": {"full_name": "example/repo"}}, "number": 1, "draft": False, "state": "open", "merged_at": None}
        other = {"head": {"sha": "b" * 40, "ref": "feature/test", "repo": {"full_name": "example/repo"}}, "number": 2, "draft": False, "state": "open", "merged_at": None}
        def second(repo, path, token):
            if path.startswith("pulls?"):
                return ([p] + [dict(other, number=i) for i in range(2, 101)] if "&page=1" in path else [dict(p, number=101)]), None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=second)
        self.assertEqual(result["pull_requests"], "AMBIGUOUS")

    def test_incomplete_pr_page_unknown(self):
        p = {"head": {"sha": SHA, "ref": "feature/test", "repo": {"full_name": "example/repo"}}, "number": 1, "draft": False, "state": "open"}
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
                    p = {"head": {"sha": SHA, "ref": "feature/test", "repo": {"full_name": "example/repo"}}, "number": 1, "state": "open",
                         "draft": False, "merged_at": None}
                    p.update(invalid_field)
                    return [p], None
                return fake(repo, path, token)
            with self.subTest(invalid_field=invalid_field):
                self.assertEqual(
                    gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["pull_requests"],
                    "UNKNOWN")


    def test_invalid_timestamp_string_unknown(self):
        for bad in ("not-a-timestamp", "2026-02-30T12:00:00Z", "2026-11-15T25:00:00Z"):
            def malformed(repo, path, token):
                if path.startswith("pulls?"):
                    return [{"head": {"sha": SHA, "ref": "feature/test",
                                      "repo": {"full_name": "example/repo"}},
                             "number": 2, "state": "closed",
                             "draft": False, "merged_at": bad}], None
                return fake(repo, path, token)
            with self.subTest(timestamp=bad):
                self.assertEqual(
                    gh.reconcile("example/repo", "feature/test", SHA, fetch=malformed)["pull_requests"],
                    "UNKNOWN")

    def test_valid_merged_timestamp_accepted(self):
        def merged(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 2, "state": "closed",
                         "draft": False, "merged_at": "2026-11-15T17:44:40Z"}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=merged)["pull_requests"], "VERIFIED")

    def test_same_sha_wrong_branch_unknown(self):
        def wrong_branch(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/another",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 2, "state": "open", "draft": True, "merged_at": None}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=wrong_branch)["pull_requests"], "UNKNOWN")

    def test_same_sha_wrong_source_repo_unknown(self):
        def wrong_repo(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "other/repo"}},
                         "number": 2, "state": "open", "draft": True, "merged_at": None}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=wrong_repo)["pull_requests"], "UNKNOWN")

    def test_incomplete_read_is_unknown(self):
        from http.client import IncompleteRead
        from unittest.mock import patch

        class BrokenResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://api.github.com/repos/example/repo/commits/" + SHA

            def read(self, *args):
                raise IncompleteRead(b"partial", 100)

        class BrokenOpener:
            def open(self, *args, **kwargs):
                return BrokenResponse()

        with patch.object(gh.request, "build_opener", return_value=BrokenOpener()):
            data, err = gh.api_get("example/repo", "commits/" + SHA, token="PLACEHOLDER")
        self.assertIsNone(data)
        self.assertIn("IncompleteRead", err)

    def test_fetch_exception_preserves_commit_evidence(self):
        from http.client import IncompleteRead

        def interrupted(repo, path, token):
            if path.startswith("pulls?"):
                raise IncompleteRead(b"partial", 100)
            return fake(repo, path, token)
        evidence = gh.reconcile("example/repo", "feature/test", SHA, fetch=interrupted)
        self.assertEqual(evidence["head"], "VERIFIED")
        self.assertEqual(evidence["pull_requests"], "UNKNOWN")
        self.assertEqual(evidence["ci"], "VERIFIED")


    def test_missing_merged_at_is_unknown(self):
        def missing(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 17, "state": "open", "draft": True}], None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=missing)
        self.assertEqual(result["pull_requests"], "UNKNOWN")

    def test_open_draft_with_merged_timestamp_is_unknown(self):
        def contradictory(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 17, "state": "open", "draft": True,
                         "merged_at": "2026-11-15T17:44:40Z"}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=contradictory)["pull_requests"],
            "UNKNOWN")

    def test_duplicate_pr_number_across_different_shas_unknown(self):
        def duplicated(repo, path, token):
            if path.startswith("pulls?"):
                return [
                    {"head": {"sha": SHA, "ref": "feature/test",
                              "repo": {"full_name": "example/repo"}},
                     "number": 17, "state": "open", "draft": True, "merged_at": None},
                    {"head": {"sha": "b" * 40, "ref": "feature/test",
                              "repo": {"full_name": "example/repo"}},
                     "number": 17, "state": "open", "draft": False, "merged_at": None}
                ], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=duplicated)["pull_requests"],
            "UNKNOWN")

    def test_json_duplicate_sha_rejected_before_commit_verification(self):
        import io
        from unittest.mock import patch

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://api.github.com/repos/example/repo/commits/" + SHA

        class Opener:
            def open(self, *args, **kwargs):
                return Response(('{"sha":"' + "b" * 40 + '","sha":"' + SHA + '"}').encode())

        with patch.object(gh.request, "build_opener", return_value=Opener()):
            payload, issue = gh.api_get("example/repo", "commits/" + SHA)
        self.assertIsNone(payload)
        self.assertIn("ValueError", issue)

    def test_extreme_json_nesting_returns_unknown_not_exception(self):
        import io
        from unittest.mock import patch

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://api.github.com/repos/example/repo/commits/" + SHA

        class Opener:
            def open(self, *args, **kwargs):
                return Response(("[" * 10000 + "0" + "]" * 10000).encode())

        with patch.object(gh.request, "build_opener", return_value=Opener()):
            payload, issue = gh.api_get("example/repo", "commits/" + SHA)
        self.assertIsNone(payload)
        self.assertIn("RecursionError", issue)


    def test_pr_short_page_link_next_cannot_verify_partial_collection(self):
        match = {"head": {"sha": SHA, "ref": "feature/test",
                          "repo": {"full_name": "example/repo"}},
                 "number": 3, "state": "open", "draft": True, "merged_at": None}
        def incomplete(repo, path, token):
            if path.startswith("pulls?"):
                if "&page=1" in path:
                    batch = gh.GitHubArray([match])
                    batch.github_link = '<https://api.github.com/repos/example/repo/pulls?page=2>; rel="next"'
                    return batch, None
                return None, "second page unavailable"
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=incomplete)
        self.assertEqual(result["pull_requests"], "UNKNOWN")
        self.assertTrue(any("page 2" in problem for problem in result["issues"]))

    def test_ci_total_count_one_with_next_link_is_unknown(self):
        def contradictory(repo, path, token):
            if path.startswith("actions/runs?"):
                batch = gh.GitHubObject({"total_count": 1, "workflow_runs": [
                    {"id": 1, "head_sha": SHA, "status": "completed",
                     "conclusion": "success"}]})
                batch.github_link = '<https://api.github.com/repos/example/repo/actions/runs?page=2>; rel="next"'
                return batch, None
            return fake(repo, path, token)
        result = gh.reconcile("example/repo", "feature/test", SHA, fetch=contradictory)
        self.assertEqual(result["ci"], "UNKNOWN")

    def test_api_get_preserves_link_header(self):
        import io
        from unittest.mock import patch
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def geturl(self):
                return "https://api.github.com/repos/example/repo/pulls?page=1"
            headers = {"Link": '<https://api.github.com/repos/example/repo/pulls?page=2>; rel="next"'}
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b"[]")
        with patch.object(gh.request, "build_opener", return_value=Opener()):
            payload, err = gh.api_get("example/repo", "pulls?page=1")
        self.assertIsNone(err)
        self.assertTrue(gh.next_link_present(payload.github_link))

    def test_malformed_pr_entry_other_than_match_unknown(self):
        def invalid(repo, path, token):
            if path.startswith("pulls?"):
                value, _ = fake(repo, path, token)
                return value + [{"head": {"sha": ""}, "number": 2,
                                 "draft": False, "state": "open", "merged_at": None}], None
            return fake(repo, path, token)
        evidence = gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)
        self.assertEqual(evidence["pull_requests"], "UNKNOWN")
        self.assertTrue(evidence["issues"])

    def test_closed_draft_merged_pr_unknown(self):
        def invalid(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 3, "state": "closed", "draft": True,
                         "merged_at": "2026-11-15T17:44:40Z"}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["pull_requests"],
            "UNKNOWN")

    def test_explicit_false_merged_with_timestamp_unknown(self):
        def invalid(repo, path, token):
            if path.startswith("pulls?"):
                return [{"head": {"sha": SHA, "ref": "feature/test",
                                  "repo": {"full_name": "example/repo"}},
                         "number": 3, "state": "closed", "draft": False, "merged": False,
                         "merged_at": "2026-11-15T17:44:40Z"}], None
            return fake(repo, path, token)
        self.assertEqual(
            gh.reconcile("example/repo", "feature/test", SHA, fetch=invalid)["pull_requests"],
            "UNKNOWN")

    def test_nonstandard_json_nan_rejected(self):
        import io
        from unittest.mock import patch
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def geturl(self):
                return "https://api.github.com/repos/example/repo/commits/" + SHA
            headers = {}
        class Opener:
            def open(self, *args, **kwargs):
                return Response(('{"sha":"' + SHA + '","bad":NaN}').encode())
        with patch.object(gh.request, "build_opener", return_value=Opener()):
            payload, err = gh.api_get("example/repo", "commits/" + SHA)
        self.assertIsNone(payload)
        self.assertIn("ValueError", err)

if __name__ == "__main__":
    unittest.main()
