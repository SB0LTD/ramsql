"""Run the unchanged ZPM PostgreSQL smoke inventory on disposable live servers.

This gate certifies smoke cases only. The broader feature/closure inventory stays
incomplete even when all smoke cases pass. Comparison is owned by ZPM audit.py.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

from test_persistence import DurableServer
from test_postgres_compat import docker


REFERENCE = ("postgres:18.3-bookworm@sha256:"
             "80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba")


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@contextmanager
def oracle(audit):
    # The ID comes only from this run. Host trust access is bound to loopback;
    # it permits the unchanged stdlib wire audit without modifying its codec.
    container = docker("run", "--detach", "--publish", "127.0.0.1::5432",
                       "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
                       "--env", "POSTGRES_DB=ramsql", REFERENCE,
                       "-c", "max_prepared_transactions=8").strip()
    try:
        port = int(docker("port", container, "5432/tcp").strip().rsplit(":", 1)[1])
        deadline = time.monotonic() + 45
        while True:
            try:
                connection = audit.Pg(port, "postgres", "ramsql")
            except (OSError, RuntimeError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Frozen PostgreSQL oracle did not start") from None
                time.sleep(0.15)
                continue
            try:
                version = connection.query("SHOW server_version_num")
                if version["error"] or version["rows"] != [["180003"]]:
                    raise RuntimeError(f"Wrong PostgreSQL inventory target: {version}")
            finally:
                connection.close()
            break
        yield port
    finally:
        subprocess.run(["docker", "rm", "--force", "--volumes", container],
                       check=True, capture_output=True, timeout=30)


def run_group(audit, harness, binary, work, cases, outside=False):
    report_path = work / "audit.json"
    with oracle(audit) as oracle_port:
        with DurableServer(binary, work / "candidate.wal", create=True, prepared=8,
                           storage_options=("--workers", "1")) as server:
            command = [sys.executable, str(harness / "audit.py"),
                       "--oracle-port", str(oracle_port), "--candidate-port", str(server.port),
                       "--user", "postgres", "--database", "ramsql", "--report", str(report_path)]
            if outside:
                command.append("--outside-transaction")
            for case in cases:
                command += ["--case", case["id"]]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=600)
            (work / "audit.stdout").write_text(completed.stdout, encoding="utf8")
            (work / "audit.stderr").write_text(completed.stderr, encoding="utf8")
            # audit.py deliberately exits 1 while complete conformance is open.
            # A report containing every selected passing case is required here.
            if completed.returncode != 1 or not report_path.is_file():
                raise RuntimeError(f"Inventory driver failed ({completed.returncode}): {completed.stderr[-2000:]}")
            report = json.loads(report_path.read_text())
            found = report["cases"]
            if len(found) != len(cases) or {c["id"] for c in found} != {c["id"] for c in cases}:
                raise RuntimeError("Inventory report omitted or duplicated selected cases")
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--harness", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    binary, harness = args.binary.resolve(), args.harness.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    manifest_bytes = (harness / "cases.json").read_bytes().replace(b"\r\n", b"\n")
    manifest = json.loads(manifest_bytes)
    cases = [c for c in manifest["cases"] if c["dialect"] == "postgresql18"]
    if not cases or any(c["scope"] not in ("transaction", "copy_protocol", "session", "cluster") for c in cases):
        raise RuntimeError("Unsupported or empty PostgreSQL inventory scope")
    spec = importlib.util.spec_from_file_location("zpm_inventory_audit", harness / "audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    summary = {"schema": 1, "qualification": "incomplete", "scope": "postgresql_smoke_only",
               "binary_sha256": sha256(binary), "audit_sha256": sha256(harness / "audit.py"),
               "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
               "reference_image": REFERENCE, "cases": [], "groups": [],
               "separate_dialect_cases": [c["id"] for c in manifest["cases"] if c["dialect"] != "postgresql18"],
               "remaining": "755 upstream records need reviewed applicability and closure; smoke success is not full conformance"}
    shared = [c for c in cases if c["scope"] in ("transaction", "copy_protocol")]
    groups = [("transaction-and-copy", shared, False)]
    groups += [(case["id"].lower().replace(".", "-"), [case], True)
               for case in cases if case["scope"] in ("session", "cluster")]
    try:
        for name, selected, outside in groups:
            work = args.work_dir / name
            work.mkdir()
            report = run_group(audit, harness, binary, work, selected, outside)
            if report["manifest_sha256"] != summary["manifest_sha256"]:
                raise RuntimeError("Manifest changed during qualification")
            summary["cases"] += report["cases"]
            summary["groups"].append({"name": name, "oracle_version": report["oracle_version"],
                                      "counts": report["counts"]})
    except Exception as error:
        summary["harness_error"] = str(error)
    summary["counts"] = dict(Counter(c["status"] for c in summary["cases"]))
    summary["smoke_gate_passed"] = (not summary.get("harness_error")
                                     and len(summary["cases"]) == len(cases)
                                     and summary["counts"] == {"smoke_pass": len(cases)})
    (args.work_dir / "postgres-inventory.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2))
    for case in summary["cases"]:
        if case["status"] != "smoke_pass":
            print(case["id"], case["status"], flush=True)
    return 0 if summary["smoke_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
