"""Fail closed until every production release requirement has recorded evidence."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    gates = json.loads((ROOT / "compatibility/release-gates.json").read_text(encoding="utf-8"))
    report = json.loads((ROOT / "compatibility/postgres-report.json").read_text(encoding="utf-8"))
    blocked = [item["id"] for item in gates["gates"] if item["status"] != "passed"]
    # A compatibility report with no probes is never evidence of parity.
    probes = report.get("probes", [])
    mismatches = sum(not probe.get("match", False) for probe in probes)
    if not probes or mismatches:
        if "postgres_compatibility" not in blocked:
            blocked.append("postgres_compatibility")
    if gates.get("release_allowed") is not True and "readiness_declaration" not in blocked:
        blocked.append("readiness_declaration")
    print(json.dumps({"production_release_allowed": not blocked,
                      "compatibility_probes": len(probes), "compatibility_gaps": mismatches,
                      "blocked_gates": blocked}, indent=2))
    return 1 if blocked else 0

if __name__ == "__main__":
    sys.exit(main())
