"""Contracts for fail-closed qualification evidence; synthetic data is test-only."""
import copy
import unittest
from unittest.mock import MagicMock, call, patch

import qualify_postgres as qualification


def synthetic_bundle(revision, binary_sha):
    entries = []
    for reference in qualification.REFERENCES.values():
        for mode in qualification.MODES:
            reports = {}
            expected, skipped = qualification.catalog(mode)
            for suite, (field, names) in expected.items():
                key = "id" if suite == "isolation" else "name"
                rows = [{key: name, "match": True, "differences": []} for name in sorted(names)]
                durable = mode == "durable-scram"
                summary = {"matching": len(rows), "mismatching": 0,
                           "probes" if suite == "gate" else "schedules" if suite == "isolation" else "executed": len(rows),
                           "drop_in_gate_passed" if suite == "gate" else "passed": True}
                report = {"ramsql_sha256": binary_sha, "reference": {"image_requested": reference},
                          "mode": {"durable": durable, "scram": durable}, field: rows, "summary": summary}
                if suite == "semantics":
                    report["skipped"] = skipped
                if suite == "copy":
                    report["divergences"] = [{"name": "explicit_row_budget_5000_rows", "runtime_rows": 512, "as_documented": True}]
                    summary["divergences"] = 1
                reports[suite] = report
            entries.append({"schema": 1, "revision": revision, "binary_sha256": binary_sha,
                            "reference": reference, "mode": mode, "passed": True, "reports": reports,
                            "note": "synthetic contract fixture, not runtime evidence"})
    return {"schema": 1, "revision": revision, "binary_sha256": binary_sha, "entries": entries}


class QualificationContracts(unittest.TestCase):
    def setUp(self):
        self.revision, self.digest = "a" * 40, "b" * 64
        self.evidence = synthetic_bundle(self.revision, self.digest)

    def validate(self, value):
        qualification.validate_bundle(value, self.revision, self.digest)

    def test_crash_fixture_errors_cannot_pass_by_matching(self):
        import test_postgres_crash_compat as crash
        ok = [{"sql": "SELECT 1", "outcome": {"sqlstate": "00000"}}]
        self.assertEqual(crash.require_success("setup", ok), ok)
        for stage in ("setup", "live owner", "consumer before crash", "after crash"):
            with self.subTest(stage=stage), self.assertRaises(RuntimeError):
                crash.require_success(stage, [{"sql": "SELECT broken", "outcome": {"sqlstate": "42883"}}])

    def test_json_surrogates_remain_reportable_without_losing_evidence(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from test_postgres_semantics import write_report
        report = {"rows": [["\ud800", "\x00", "é", None]], "sqlstate": "00000"}
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantics.json"
            write_report(path, report)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)

    def test_complete_matrix_and_explicit_restart_skips(self):
        self.validate(self.evidence)
        self.assertEqual(len(self.evidence["entries"]), 4)
        self.assertEqual(len(self.evidence["entries"][0]["reports"]["semantics"]["skipped"]), 11)
        self.assertIn("q04_concat_restart", {case["name"] for case in self.evidence["entries"][0]["reports"]["semantics"]["skipped"]})

    def test_missing_or_duplicated_matrix_entry(self):
        for operation in (lambda b: b["entries"].pop(), lambda b: b["entries"].append(b["entries"][0]),
                          lambda b: b["entries"].__setitem__(1, b["entries"][0])):
            bad = copy.deepcopy(self.evidence)
            operation(bad)
            with self.assertRaises(ValueError):
                self.validate(bad)

    def test_wrong_source_binary_reference_and_mode(self):
        for key, value in (("revision", "c" * 40), ("binary_sha256", "d" * 64),
                           ("reference", "postgres:latest"), ("mode", "insecure-durable")):
            bad = copy.deepcopy(self.evidence)
            bad["entries"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(bad)

    def test_summary_cannot_hide_bad_raw_cases(self):
        for suite, field in (("gate", "probes"), ("semantics", "cases"), ("isolation", "schedules"), ("copy", "cases")):
            for mutation in ("drop", "duplicate", "difference", "failed", "skip", "wrong-binary", "harness-error"):
                bad = copy.deepcopy(self.evidence)
                report = bad["entries"][0]["reports"][suite]
                if mutation == "drop": report[field].pop()
                elif mutation == "duplicate": report[field][-1] = report[field][0]
                elif mutation == "difference": report[field][0]["differences"] = [{"path": "/rows"}]
                elif mutation == "failed": report[field][0]["match"] = False
                elif mutation == "skip": report["skipped"] = [{"reason": "hidden failure"}]
                elif mutation == "wrong-binary": report["ramsql_sha256"] = "c" * 64
                elif mutation == "harness-error": report["harness_error"] = {"message": "process crashed"}
                with self.subTest(suite=suite, mutation=mutation), self.assertRaises(ValueError):
                    self.validate(bad)

    def test_crash_suite_required_only_for_durable_mode(self):
        bad = copy.deepcopy(self.evidence)
        del bad["entries"][1]["reports"]["crash"]
        with self.assertRaisesRegex(ValueError, "suite missing"):
            self.validate(bad)

    def test_unknown_copy_divergence_is_failure(self):
        bad = copy.deepcopy(self.evidence)
        bad["entries"][0]["reports"]["copy"]["divergences"].append({"name": "new-incompatibility", "as_documented": True})
        with self.assertRaisesRegex(ValueError, "divergence changed"):
            self.validate(bad)

    def test_reference_crash_never_sends_a_clean_shutdown_signal(self):
        from test_postgres_compat import restart_native
        connection = {"port": 1234}
        with patch("test_postgres_compat.docker", side_effect=["owned", "owned", "127.0.0.1:9876"]) as docker:
            restart_native(connection, {"container_id": "test-owned-id"}, MagicMock())
        self.assertEqual(docker.call_args_list[:2], [call("kill", "--signal=KILL", "test-owned-id"), call("start", "test-owned-id")])
        self.assertEqual(connection["port"], 9876)

    def test_reference_survives_kill_and_is_removed_only_on_context_exit(self):
        from test_postgres_compat import native_postgres
        inspection = '[{"Id":"sha256:test", "RepoDigests":[], "Os":"linux", "Architecture":"amd64"}]'
        with patch("test_postgres_compat.docker", side_effect=[inspection, "test-owned-id", "127.0.0.1:9876"]) as docker, \
                patch("test_postgres_compat.subprocess.run") as cleanup:
            with native_postgres("pinned-test-image", MagicMock()):
                run = docker.call_args_list[1].args
                self.assertNotIn("--rm", run)
                cleanup.assert_not_called()
            self.assertEqual(cleanup.call_args.args[0], ["docker", "rm", "--force", "--volumes", "test-owned-id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
