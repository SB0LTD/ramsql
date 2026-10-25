"""Compare native revisions with checked, duration-calibrated, alternating runs.

Each trial checks every query result against the independent benchmark model.
Calibration logs are retained but excluded from reported comparison samples.
The optional permuted layout changes insertion order while preserving rows/SQL.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import time

from benchmark_review import CASES, validate_samples, validate_stream_samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", action="append", required=True,
                        help="LABEL=/absolute/path/to/binary (repeat for each revision)")
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--minimum-ms", type=int, default=100)
    parser.add_argument("--permuted", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.cycles < 2 or args.minimum_ms < 50 or args.rows < 64:
        parser.error("require >=2 cycles, >=50ms trials and >=64 rows")
    binaries = {}
    for spec in args.binary:
        label, separator, value = spec.partition("=")
        if not separator or not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label) or label in binaries:
            parser.error("binary labels must be unique filename-safe names")
        binary = Path(value).resolve(strict=True)
        binaries[label] = {"path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    if len(binaries) < 2:
        parser.error("provide at least two binaries")
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    cpu = platform.processor()
    if Path("/proc/cpuinfo").exists():
        cpu = next((line.partition(":")[2].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")), cpu)
    report = {"rows": args.rows, "minimum_trial_ms": args.minimum_ms, "cpu": cpu,
              "layout": "permuted" if args.permuted else "ordered",
              "os": platform.platform(), "started_unix": time.time(), "binaries": binaries,
              "scope": "Unprofiled native fixture; calibration excluded; alternating revision order; independently checked.",
              "calibration": [], "runs": [], "summary": {}}

    def save():
        (folder/"results.json").write_text(json.dumps(report, indent=2)+"\n")

    def run(label, case, iterations, filename):
        command = [binaries[label]["path"], str(args.rows), str(iterations), "--case", case]
        if args.permuted:
            command.append("--permuted")
        began = time.monotonic()
        with (folder/filename).open("wb") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=True)
        records = [json.loads(line) for line in (folder/filename).read_text().splitlines() if line.startswith("{")]
        samples = [record for record in records if record.get("case") == case]
        assert all(sample.get("passed") is True for sample in samples), "benchmark reported failure"
        validate_samples(case, samples, args.rows, iterations)
        validate_stream_samples(case, samples, args.rows)
        return {"label": label, "case": case, "iterations": iterations,
                "command": command, "wall_seconds": time.monotonic()-began,
                "raw_log": filename, "samples": samples}

    save()
    for case in args.case or CASES:
        counts = {}
        # Seed/import stays outside measured trials, including calibration.
        for label in binaries:
            count = 1
            for attempt in range(8):
                measured = run(label, case, count, f"calibrate-{case}-{label}-{attempt}.jsonl")
                report["calibration"].append(measured); save()
                elapsed = min(sample["elapsed_ns"] for sample in measured["samples"])
                if elapsed >= args.minimum_ms*1000000:
                    break
                count = max(count+1, math.ceil(count*args.minimum_ms*1000000/elapsed*1.25))
                if count > 10000000:
                    raise RuntimeError("calibration exceeded iteration safety limit")
            else:
                raise RuntimeError("trial duration did not converge")
            counts[label] = count
        for cycle in range(args.cycles):
            order = list(binaries)
            if cycle % 2:
                order.reverse()
            for label in order:
                measured = run(label, case, counts[label], f"cycle-{cycle}-{case}-{label}.jsonl")
                measured["cycle"] = cycle
                report["runs"].append(measured); save()
        report["summary"][case] = {}
        for label in binaries:
            samples = [sample for run_record in report["runs"]
                       if run_record["case"] == case and run_record["label"] == label
                       for sample in run_record["samples"]]
            rates = [s["operations"]*1000000000/s["elapsed_ns"] for s in samples]
            report["summary"][case][label] = {"median_ops_s": statistics.median(rates),
                "minimum_ops_s": min(rates), "maximum_ops_s": max(rates),
                "minimum_trial_ms": min(s["elapsed_ns"] for s in samples)/1000000,
                "trials": len(samples), "iterations": counts[label]}
        save()
        print(case, json.dumps(report["summary"][case]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
