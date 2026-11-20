"""Kill and reopen RAMSQL on both sides of a live checkpoint cut."""
import argparse
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from test_persistence import DurableServer


PHASES = ("staged", "before_selection", "after_selection", "after_handoff")
IDENTITY = "00112233445566778899aabbccddeeff"


class CheckpointServer(DurableServer):
    def command(self):
        return [str(self.binary), "--serve", "--insecure-local", "--port", "0",
                "--checkpoint-stem", str(self.wal), "--checkpoint-id", IDENTITY,
                *self.storage_options]


def execute(server, sql, rows=None):
    pg = server.connect()
    try:
        pg.ok(sql, rows)
    finally:
        pg.close()


def selector(stem):
    data = stem.with_suffix(".manifest").read_bytes()
    assert len(data) == 160 and data[:8] == b"RAMSQLM1", data[:8]
    return struct.unpack_from("<Q", data, 48)[0]


def server_crashes(binary, root):
    original = root / "before.wal"
    stem = root / "checkpoint"
    with DurableServer(binary, original, create=True, storage_options=("--workers", "1")) as server:
        execute(server, "CREATE TABLE journal (id INTEGER PRIMARY KEY)")
        execute(server, "INSERT INTO journal VALUES (1)")
    original_bytes = original.read_bytes()
    bootstrap = [str(binary), "--data", str(original), "--bootstrap-checkpoint",
                 "--checkpoint-stem", str(stem), "--checkpoint-id", IDENTITY]
    created = subprocess.run(bootstrap, capture_output=True, text=True, timeout=40)
    assert created.returncode == 0, created.stderr
    assert selector(stem) == 1 and original.read_bytes() == original_bytes
    again = subprocess.run(bootstrap, capture_output=True, text=True, timeout=40)
    assert again.returncode != 0 and "CheckpointAlreadyExists" in again.stderr, again.stderr
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "INSERT INTO journal VALUES (2)")
        assert selector(stem) == 1
    before = stem.with_suffix(".g1.wal")
    before_cut = before.read_bytes()
    with CheckpointServer(binary, stem, storage_options=("--workers", "1", "--checkpoint-every", "1")) as server:
        execute(server, "SELECT id FROM journal ORDER BY id", [["1"], ["2"]])
        busy = subprocess.run(CheckpointServer(binary, stem).command(), capture_output=True, text=True, timeout=40)
        assert busy.returncode != 0 and "DataDirectoryBusy" in busy.stderr and "RAMSQL postgres" not in busy.stdout
        execute(server, "INSERT INTO journal VALUES (3)")
        assert selector(stem) == 2
        old_wal = before.read_bytes()
        assert len(old_wal) > len(before_cut) and old_wal.startswith(before_cut)
        execute(server, "INSERT INTO journal VALUES (4)")
        assert selector(stem) == 3
    assert before.read_bytes() == old_wal
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "SELECT id FROM journal ORDER BY id", [["1"], ["2"], ["3"], ["4"]])
        execute(server, "INSERT INTO journal VALUES (5)")
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "SELECT id FROM journal ORDER BY id", [["1"], ["2"], ["3"], ["4"], ["5"]])

    with CheckpointServer(binary, stem, storage_options=("--workers", "1", "--checkpoint-every", "1")) as server:
        execute(server, "CREATE SCHEMA unsupported_by_image")
        assert selector(stem) == 3
        execute(server, "INSERT INTO journal VALUES (6)")
        assert selector(stem) == 3
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "SELECT id FROM journal ORDER BY id", [[str(i)] for i in range(1, 7)])

    manifest = stem.with_suffix(".manifest")
    manifest.rename(stem.with_suffix(".manifest.hidden"))
    missed = subprocess.run(CheckpointServer(binary, stem).command(), capture_output=True, text=True, timeout=40)
    assert missed.returncode != 0 and "FileNotFound" in missed.stderr and "RAMSQL postgres" not in missed.stdout
    assert original.read_bytes() == original_bytes and before.read_bytes() == old_wal


def sequence_recovery(binary, root):
    original = root / "sequence.wal"
    stem = root / "sequence"
    with DurableServer(binary, original, create=True, storage_options=("--workers", "1")) as server:
        execute(server, "CREATE SEQUENCE allocated START 10 INCREMENT 2 CACHE 4")
        execute(server, "SELECT nextval('allocated')", [["10"]])
        execute(server, "CREATE TABLE reservations (id INT PRIMARY KEY)")
        execute(server, "INSERT INTO reservations VALUES (10)")
    bootstrap = subprocess.run(
        [str(binary), "--data", str(original), "--bootstrap-checkpoint",
         "--checkpoint-stem", str(stem), "--checkpoint-id", IDENTITY],
        capture_output=True, text=True, timeout=40,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr

    with CheckpointServer(binary, stem, storage_options=("--workers", "1", "--checkpoint-every", "1")) as server:
        execute(server, "INSERT INTO reservations SELECT nextval('allocated')::int4")
        assert selector(stem) >= 2
        execute(server, "INSERT INTO reservations SELECT nextval('allocated')::int4")
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "SELECT count(*) = 3 AND max(id) > 16 FROM reservations", [["t"]])
        execute(server, "SELECT nextval('allocated') > (SELECT max(id) FROM reservations)", [["t"]])


def sequence_only_rotation(binary, root):
    original = root / "sequence-only.wal"
    stem = root / "sequence-only"
    with DurableServer(binary, original, create=True, storage_options=("--workers", "1")) as server:
        execute(server, "CREATE SEQUENCE ticket START 20")
    bootstrap = subprocess.run(
        [str(binary), "--data", str(original), "--bootstrap-checkpoint",
         "--checkpoint-stem", str(stem), "--checkpoint-id", IDENTITY],
        capture_output=True, text=True, timeout=40,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    with CheckpointServer(binary, stem, storage_options=("--workers", "1", "--checkpoint-every", "1")) as server:
        execute(server, "SELECT nextval('ticket')", [["20"]])
        assert selector(stem) == 2
    with CheckpointServer(binary, stem, storage_options=("--workers", "1")) as server:
        execute(server, "SELECT nextval('ticket') > 20", [["t"]])


def concurrent_writers(binary, root):
    original = root / "workers.wal"
    stem = root / "workers"
    with DurableServer(binary, original, create=True, storage_options=("--workers", "1")) as server:
        execute(server, "CREATE TABLE journal (id INTEGER PRIMARY KEY)")
    bootstrap = subprocess.run(
        [str(binary), "--data", str(original), "--bootstrap-checkpoint",
         "--checkpoint-stem", str(stem), "--checkpoint-id", IDENTITY],
        capture_output=True, text=True, timeout=40,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr

    def insert_partition(server, start):
        pg = server.connect()
        try:
            for row in range(start, start + 6):
                pg.ok(f"INSERT INTO journal VALUES ({row})")
        finally:
            pg.close()

    with CheckpointServer(binary, stem, storage_options=("--workers", "4", "--checkpoint-every", "8")) as server:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in [pool.submit(insert_partition, server, i) for i in range(1, 25, 6)]:
                result.result()
        assert selector(stem) == 4
    with CheckpointServer(binary, stem, storage_options=("--workers", "4")) as server:
        execute(server, "SELECT id FROM journal ORDER BY id", [[str(i)] for i in range(1, 25)])


def boundary_crashes(fixture, root):
    for phase in PHASES:
        stem = root / phase
        seeded = subprocess.run([str(fixture), "seed", str(stem), phase], capture_output=True, text=True, timeout=40)
        assert seeded.returncode == 0, seeded.stderr
        with subprocess.Popen([str(fixture), "fault", str(stem), phase],
                              stderr=subprocess.PIPE, text=True) as process:
            result = queue.Queue()
            reader = threading.Thread(target=lambda: result.put(process.stderr.readline()), daemon=True)
            reader.start()
            try:
                line = result.get(timeout=30).strip()
                assert line == "CHECKPOINT_CRASH_BOUNDARY " + phase, line
                assert process.poll() is None
            finally:
                process.kill()
                process.wait(timeout=10)
                reader.join(timeout=2)
        verified = subprocess.run([str(fixture), "verify", str(stem), phase],
                                  capture_output=True, text=True, timeout=40)
        assert verified.returncode == 0, verified.stderr
        assert "CHECKPOINT_CRASH_RECOVERED " + phase + " 8 assertions" in verified.stderr, verified.stderr
        assert selector(stem) == 3


def selector_failure(fixture, root):
    stem = root / "selector_failure"
    for action in ("seed", "fail_selection", "verify"):
        result = subprocess.run([str(fixture), action, str(stem), "before_selection"],
                                capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, (action, result.stderr)
        assert selector(stem) == (3 if action == "verify" else 1)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1] / "sig-out" / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    args.add_argument("--binary", type=Path, default=root / ("ramsql" + suffix))
    args.add_argument("--fixture", type=Path)
    args.add_argument("--concurrent", action="store_true")
    options = args.parse_args()
    output = Path(tempfile.mkdtemp(prefix="ramsql-checkpoint-"))
    server_crashes(options.binary.resolve(), output)
    sequence_recovery(options.binary.resolve(), output)
    sequence_only_rotation(options.binary.resolve(), output)
    if options.concurrent:
        concurrent_writers(options.binary.resolve(), output)
    if options.fixture:
        boundary_crashes(options.fixture.resolve(), output)
        selector_failure(options.fixture.resolve(), output)
    print(json.dumps({"suite": "checkpoint_runtime", "pass": True, "server_crashes": 6,
                      "sequence_recovery_tested": True,
                      "publication_boundary_crashes": len(PHASES) if options.fixture else 0,
                      "concurrent_writers": 4 if options.concurrent else 0,
                      "selector_failure_tested": options.fixture is not None,
                      "evidence_directory": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
