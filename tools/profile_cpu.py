"""Sample one correctness-checked native workload after its seed barrier.

Linux perf cpu-clock sampling works without hardware PMU support. It is a
diagnostic run, not a throughput comparison: keep separate unprofiled timings.
No profiler or Python dependency is linked into RAMSQL.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import signal
import subprocess
import tempfile
import time

from benchmark_review import CASES, validate_samples, validate_stream_samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--perf", type=Path, required=True)
    parser.add_argument("--case", choices=(*CASES, "load"), required=True)
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--frequency", type=int, default=499)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    # DrvFS/Windows workspace mounts do not support FIFOs. Keep control pipes
    # on the native Linux temporary filesystem; preserve their owned directory.
    controls = Path(tempfile.mkdtemp(prefix="ramsql-perf-control-"))
    control, ack = controls/"control.fifo", controls/"ack.fifo"
    os.mkfifo(control); os.mkfifo(ack)
    ctl_fd = os.open(control, os.O_RDWR | os.O_NONBLOCK)
    ack_fd = os.open(ack, os.O_RDWR | os.O_NONBLOCK)
    command = [str(args.binary.resolve()), str(args.rows), str(args.iterations), "--profile-ready"]
    command += ["--load-only"] if args.case == "load" else ["--case", args.case]
    profiled = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
    recorder = None
    started = time.time()
    try:
        with (folder/"benchmark.jsonl").open("w", encoding="utf-8") as log, (folder/"perf.log").open("w") as perf_log:
            # The target stays blocked on stdin after this line; perf's control
            # acknowledgement establishes sampling before the workload starts.
            for line in profiled.stdout:
                log.write(line); log.flush()
                if line.strip() == "# profile-ready": break
            else: raise RuntimeError("target exited without its profiling barrier")
            recorder = subprocess.Popen([str(args.perf.resolve()), "record", "-e", "cpu-clock:u",
                "-F", str(args.frequency), "--call-graph", "dwarf,8192", "--delay=-1",
                f"--control=fifo:{control},{ack}", "-p", str(profiled.pid),
                "-o", str(folder/"perf.data")], stdout=perf_log, stderr=subprocess.STDOUT)
            os.write(ctl_fd, b"enable\n")
            if not select.select([ack_fd], [], [], 20)[0]:
                raise RuntimeError("perf did not acknowledge sampling; see perf.log")
            if b"ack" not in os.read(ack_fd, 1024): raise RuntimeError("invalid perf acknowledgement")
            profiled.stdin.write("\n"); profiled.stdin.flush(); profiled.stdin.close()
            for line in profiled.stdout: log.write(line)
            if profiled.wait(timeout=1200): raise RuntimeError("profiled benchmark failed")
            # perf normally exits when its target does. SIGINT finalizes only
            # this owned recorder if it still waits for another event.
            try: status = recorder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                recorder.send_signal(signal.SIGINT); status = recorder.wait(timeout=10)
            if status not in (0, -signal.SIGINT): raise RuntimeError("perf recording failed")
        records = [json.loads(x) for x in (folder/"benchmark.jsonl").read_text().splitlines() if x.startswith("{")]
        samples = [x for x in records if "case" in x]
        if args.case != "load":
            assert all(s.get("passed") is True and s["case"] == args.case for s in samples)
            validate_samples(args.case, samples, args.rows, args.iterations)
            validate_stream_samples(args.case, samples, args.rows)
        with (folder/"self.txt").open("w") as out:
            subprocess.run([str(args.perf.resolve()), "report", "--stdio", "--no-children",
                "-g", "none", "--percent-limit", "0.5", "--sort", "symbol", "-i", str(folder/"perf.data")],
                stdout=out, stderr=subprocess.STDOUT, check=True)
        with (folder/"callgraph.txt").open("w") as out:
            subprocess.run([str(args.perf.resolve()), "report", "--stdio", "--children",
                "--percent-limit", "1", "-i", str(folder/"perf.data")],
                stdout=out, stderr=subprocess.STDOUT, check=True)
        (folder/"metadata.json").write_text(json.dumps({"command":command,"case":args.case,
            "rows":args.rows,"iterations":args.iterations,"frequency":args.frequency,
            "event":"cpu-clock:u","hardware_PMU_sampled":False,"control_directory":str(controls),
            "os":platform.platform(),"binary_sha256":hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            "dependency_manifest":(Path(__file__).resolve().parents[1]/"build.sig.zon").read_text(),
            "benchmark_source_sha256":hashlib.sha256((Path(__file__).resolve().parents[1]/"src/review_benchmark.sig").read_bytes()).hexdigest(),
            "started_unix":started,"duration_seconds":time.time()-started,
            "scope":"Sampled user CPU after acknowledged barrier; profiling overhead included; not comparison throughput."},indent=2)+"\n")
        print("Profile complete:", folder)
    finally:
        if profiled.poll() is None: profiled.kill(); profiled.wait()
        if recorder is not None and recorder.poll() is None:
            recorder.send_signal(signal.SIGINT); recorder.wait(timeout=10)
        os.close(ctl_fd); os.close(ack_fd)
    return 0


if __name__ == "__main__": raise SystemExit(main())
