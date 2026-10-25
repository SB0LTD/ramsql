"""Reproducible PostgreSQL qualification, shared by CI and exact-artifact releases.

Reports include every executed case and documented divergence. A successful
summary never substitutes for checking completeness, identities and raw results.
Reference images are immutable; Docker pulls occur only with --pull-reference.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = {
    "16": "postgres@sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36",
    "18": "postgres@sha256:4da1a4828be12604092fa55311276f08f9224a74a62dcb4708bd7439e2a03911",
}
MODES = ("memory", "durable-scram")
SEMANTIC_SUITES = ("q01", "q02", "q03", "q04", "q06", "q07", "q08", "q09", "q10", "p01")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def catalog(mode):
    # Import the executable case definitions, not a second hand-maintained list.
    import test_postgres_compat as gate
    import test_postgres_semantics as semantics
    import test_postgres_isolation as isolation
    import test_postgres_copy as copy
    import test_postgres_crash_compat as crash
    cases, skipped = semantics.select_cases(SEMANTIC_SUITES, 5.1, mode == "durable-scram")
    require(len(cases) >= (151 if mode == "durable-scram" else 141), "semantic coverage shrank")
    schedules = set(isolation.SCHEDULES) - {"trivial_two_actor"}
    require(len(schedules) >= 44, "isolation coverage shrank")
    expected = {
        "gate": ("probes", {case.name for case in gate.CASES}),
        "semantics": ("cases", {case.name for case in cases}),
        "isolation": ("schedules", schedules),
        "copy": ("cases", {name for name, _ in copy.CASES}),
    }
    require(len(expected["gate"][1]) >= 37 and len(expected["copy"][1]) >= 11, "probe/COPY coverage shrank")
    if mode == "durable-scram":
        expected["crash"] = ("cases", {case.name for case in crash.CASES})
        require(len(expected["crash"][1]) >= 2, "crash coverage shrank")
    return expected, skipped


def validate_entry(entry, revision, binary_sha, reference, mode):
    require(entry.get("schema") == 1 and entry.get("passed") is True, "qualification did not pass")
    require(entry.get("revision") == revision and entry.get("binary_sha256") == binary_sha,
            "qualification source/binary identity mismatch")
    require(entry.get("reference") == reference and entry.get("mode") == mode,
            "qualification reference/mode mismatch")
    expected, skipped = catalog(mode)
    reports = entry.get("reports", {})
    require(set(reports) == set(expected), "qualification suite missing or unexpected")
    for suite, (field, names) in expected.items():
        report = reports[suite]
        require(not report.get("harness_error"), f"{suite}: harness error")
        require(report.get("ramsql_sha256") == binary_sha, f"{suite}: wrong binary")
        require(report.get("reference", {}).get("image_requested") == reference, f"{suite}: wrong reference")
        actual_mode = report.get("mode", {})
        durable = mode == "durable-scram"
        require(actual_mode.get("durable") is durable and actual_mode.get("scram") is durable,
                f"{suite}: wrong durability/authentication mode")
        summary = report.get("summary", {})
        passed = summary.get("drop_in_gate_passed") if suite == "gate" else summary.get("passed")
        require(passed is True and not summary.get("incomplete") and summary.get("mismatching") == 0,
                f"{suite}: failed/incomplete summary")
        rows = report.get(field, [])
        ids = [row.get("id") if suite == "isolation" else row.get("name") for row in rows]
        require(len(ids) == len(names) and set(ids) == names, f"{suite}: missing, duplicate or unexpected cases")
        require(summary.get("matching") == len(rows), f"{suite}: incorrect summary count")
        count_field = "probes" if suite == "gate" else "schedules" if suite == "isolation" else "executed"
        require(summary.get(count_field) == len(rows), f"{suite}: incorrect executed count")
        require(all(row.get("match") is True and not row.get("differences") for row in rows),
                f"{suite}: raw case failed despite summary")
        if suite == "semantics":
            require(report.get("skipped", []) == skipped, "semantic skips differ from the explicit restart-only exclusions")
        else:
            require(not report.get("skipped"), f"{suite}: unexpected skipped cases")
        if suite == "copy":
            divergences = report.get("divergences", [])
            require(len(divergences) == 1 and divergences[0].get("name") == "default_profile_capacity_5000_rows"
                    and divergences[0].get("as_documented") is True, "COPY capacity divergence changed")
            require(summary.get("divergences") == 1, "COPY divergence absent from summary")


def validate_bundle(bundle, revision, binary_sha):
    require(bundle.get("schema") == 1 and bundle.get("revision") == revision
            and bundle.get("binary_sha256") == binary_sha, "qualification bundle identity mismatch")
    entries = bundle.get("entries", [])
    expected = {(reference, mode) for reference in REFERENCES.values() for mode in MODES}
    actual = [(item.get("reference"), item.get("mode")) for item in entries]
    require(len(actual) == len(expected) and set(actual) == expected, "qualification matrix incomplete or duplicated")
    for entry in entries:
        validate_entry(entry, revision, binary_sha, entry["reference"], entry["mode"])


def run(args):
    binary = args.binary.resolve(strict=True)
    reference = REFERENCES[args.reference]
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(revision == args.revision, "checkout differs from requested qualification revision")
    binary_sha = sha256(binary)
    expected, _ = catalog(args.mode)
    extra = ["--durable", "--scram"] if args.mode == "durable-scram" else []
    commands = {
        "gate": ["test_postgres_compat.py", *extra],
        "semantics": ["test_postgres_semantics.py", "--suite", ",".join(SEMANTIC_SUITES), "--step", "5.1", *extra],
        "isolation": ["test_postgres_isolation.py", "--schedule", ",".join(sorted(expected["isolation"][1])), *extra],
        "copy": ["test_postgres_copy.py", *extra],
        "crash": ["test_postgres_crash_compat.py"],
    }
    entry = {"schema": 1, "revision": revision, "binary_sha256": binary_sha,
             "reference": reference, "mode": args.mode, "passed": False, "reports": {},
             "started_utc": datetime.now(timezone.utc).isoformat(), "commands": {}}
    try:
        if args.pull_reference:
            subprocess.run(["docker", "pull", "--platform", "linux/amd64", reference], check=True, timeout=300)
        for suite in expected:
            script, *flags = commands[suite]
            report_path = output / (suite + ".json")
            command = [sys.executable, str(ROOT / "tools" / script), "--binary", str(binary), "--image", reference,
                       "--work-dir", str(output / "data"), "--report", str(report_path), *flags]
            began = time.monotonic()
            print(f"QUALIFY {args.reference}/{args.mode}/{suite}", flush=True)
            # Keep raw command output even when a process crashes before JSON.
            with (output / (suite + ".log")).open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
            entry["commands"][suite] = {"exit_code": completed.returncode, "seconds": round(time.monotonic() - began, 3)}
            if report_path.exists():
                entry["reports"][suite] = json.loads(report_path.read_text(encoding="utf-8"))
            require(completed.returncode == 0, f"{suite} failed; see {output / (suite + '.log')}")
        entry["passed"] = True
        validate_entry(entry, revision, binary_sha, reference, args.mode)
    except Exception as error:
        entry["passed"] = False
        entry["qualification_error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        entry["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / f"qualification-pg{args.reference}-{args.mode}.json").write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    single = commands.add_parser("run")
    single.add_argument("--reference", choices=REFERENCES, required=True)
    single.add_argument("--mode", choices=MODES, required=True)
    single.add_argument("--binary", type=Path, required=True)
    single.add_argument("--revision", required=True)
    single.add_argument("--output", type=Path, required=True)
    single.add_argument("--pull-reference", action="store_true")
    merge = commands.add_parser("merge")
    merge.add_argument("--input", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument("--revision", required=True)
    merge.add_argument("--binary-sha256", required=True)
    args = parser.parse_args()
    if args.command == "run":
        run(args)
    else:
        bundle = {"schema": 1, "revision": args.revision, "binary_sha256": args.binary_sha256,
                  "entries": [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.input.rglob("qualification-*.json"))]}
        validate_bundle(bundle, args.revision, args.binary_sha256)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
