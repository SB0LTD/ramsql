"""Exact hosted PostgreSQL capacity, worker, overflow and lock/cancel contract."""

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import select
import struct
import subprocess
import time

from test_server import PG, Server, errors, expect_closed, expect_error, query_packet, startup_packet


CLIENTS = 128
PROFILE = ("--rows", "64", "--work-rows", "64", "--result-rows", "32")


def read_plan(binary, mode, flags):
    return json.loads(subprocess.check_output([str(binary), mode, *flags], timeout=30))


def reject(binary, mode, flags, error):
    result = subprocess.run([str(binary), mode, *flags], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and not result.stdout, result
    assert error in result.stderr, result


def assert_overflow(server):
    with server.raw() as sock:
        pg = PG(sock)
        pg.sock.sendall(startup_packet())
        expect_error([pg.message()], "53300")
        expect_closed(pg)


def admit(server, owners):
    pg = PG(server.raw())
    owners.callback(pg.close)
    keys = [struct.unpack("!II", body) for tag, body in pg.startup() if tag == b"K"]
    assert len(keys) == 1
    return pg, keys[0]


def wait_is_blocked(pg, delay=0.04):
    assert not select.select([pg.sock], [], [], delay)[0], "lock wait returned before the blocker released"


def run_wire(binary, workers):
    flags = (*PROFILE, "--workers", str(workers), "--max-clients", str(CLIENTS))
    plan = read_plan(binary, "--machine-capacity", flags)
    caps = read_plan(binary, "--capabilities", flags)
    assert caps["compiled_clients"] == CLIENTS and caps["clients"] == CLIENTS
    assert plan["transaction_slots"] == 130 and plan["read_workers"] == workers
    assert plan["active_admission_fits"] and plan["startup_fits"]
    assert plan["startup_region_bytes"] < plan["active_admission_bytes"] <= plan["new_region_bytes"]

    with Server(binary, max_clients=CLIENTS, idle_ms=120_000,
                extra_arguments=(*PROFILE, "--workers", str(workers))) as server, ExitStack() as owners:
        sessions = [admit(server, owners) for _ in range(CLIENTS)]
        assert sorted(key[0] for _, key in sessions) == list(range(1, CLIENTS + 1))
        assert_overflow(server)
        low, high = sessions[0][0], sessions[-1][0]
        next_high = sessions[-2][0]
        low.ok("CREATE TABLE active(id INT PRIMARY KEY, payload INT)")
        low.ok("INSERT INTO active VALUES (1,10),(2,20)")
        for pg, key in sessions:
            pg.ok(f"SELECT {key[0]}", [[str(key[0])]])
            pg.ok("BEGIN", status=b"T")

        low.ok("UPDATE active SET payload=11 WHERE id=1", status=b"T")
        high.sock.sendall(query_packet("UPDATE active SET payload=99 WHERE id=1"))
        wait_is_blocked(high)
        next_high.ok("SELECT payload FROM active WHERE id=1", [["10"]], status=b"T")
        with server.raw() as cancel:
            key = sessions[-1][1]
            packet = struct.pack("!IIII", 16, 80877102, *key)
            cancel.sendall(packet[:5])
            time.sleep(0.005)
            cancel.sendall(packet[5:])
            expect_closed(PG(cancel))
        expect_error(high.ready(), "57014")
        high.ok("ROLLBACK")

        next_high.ok("UPDATE active SET payload=21 WHERE id=2", status=b"T")
        low.sock.sendall(query_packet("UPDATE active SET payload=22 WHERE id=2"))
        wait_is_blocked(low)
        next_high.sock.sendall(query_packet("UPDATE active SET payload=12 WHERE id=1"))
        pending = {low.sock: low, next_high.sock: next_high}
        victim = winner = None
        while pending:
            ready, _, _ = select.select(list(pending), [], [], 5)
            assert ready, "deadlock across transaction bitset words never resolved"
            for sock in ready:
                pg = pending.pop(sock)
                response = pg.ready()
                if errors(response):
                    expect_error(response, "40P01")
                    assert victim is None
                    victim = pg
                    victim.ok("ROLLBACK")
                else:
                    assert winner is None and response[-1] == (b"Z", b"T")
                    assert any(tag == b"C" for tag, _ in response)
                    winner = pg
        assert winner is not None and victim is not None
        winner.ok("COMMIT")
        for pg, _ in sessions[1:-2]:
            pg.ok("ROLLBACK")
        values = [["1", "11"], ["2", "22"]] if winner is low else [["1", "12"], ["2", "21"]]
        high.ok("SELECT id,payload FROM active ORDER BY id", values)

        high.close()
        # Its Terminate can be ahead of the acceptor's retire notification.
        deadline = time.monotonic() + 5
        while True:
            with server.raw() as sock:
                candidate = PG(sock)
                candidate.sock.sendall(startup_packet())
                first = candidate.message()
                if first[0] == b"E":
                    expect_error([first], "53300")
                    if time.monotonic() >= deadline:
                        raise AssertionError("closed high slot did not return to admission")
                    time.sleep(0.01)
                    continue
                response = [first, *candidate.ready()]
                key, = [struct.unpack("!II", body) for tag, body in response if tag == b"K"]
                assert key[0] == CLIENTS
                candidate.ok("SELECT 128", [["128"]])
                break
    return {
        "workers": workers, "clients": len(sessions), "highest_backend_id": CLIENTS,
        "overflow": "53300", "cancel": "57014", "deadlock": "40P01", "slot_reused": True,
        "admission_plan": plan,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    default = read_plan(binary, "--capabilities", ())
    assert default["clients"] == 16 and default["compiled_clients"] == CLIENTS
    invalid = ("--max-clients", str(CLIENTS), *PROFILE)
    for options in (("--protocol", "mysql"), ("--mssql-port", "4444"), ("--database-cluster",),
                    ("--max-prepared-transactions", "1")):
        reject(binary, "--machine-capacity", (*invalid, *options), "InvalidServerOptions")
    reject(binary, "--machine-capacity", ("--max-clients", "129"), "InvalidServerOptions")
    low_memory = (*invalid, "--workers", "1")
    plan = read_plan(binary, "--machine-capacity", low_memory)
    cropped = (*low_memory, "--memory-budget", str(plan["active_admission_bytes"] - 1))
    refused = read_plan(binary, "--machine-capacity", cropped)
    assert refused["startup_fits"] and not refused["active_admission_fits"], refused
    reject(binary, "--serve", (*cropped, "--memory", "--insecure-local", "--port", "0"), "BudgetExceeded")
    native_workers = default["native_workers_available"]
    report = {"suite": "hosted_128_admission", "passed": False, "profiles": [],
              "native_workers_available": native_workers}
    with binary.open("rb") as handle:
        report["binary_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    try:
        for workers in ((1, 4) if native_workers else (1,)):
            report["profiles"].append(run_wire(binary, workers))
        if not native_workers:
            reject(binary, "--machine-capacity", (*invalid, "--workers", "4"), "WorkersUnsupportedOnPlatform")
            report["parallel_worker_refusal"] = "WorkersUnsupportedOnPlatform"
        report["passed"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Hosted PostgreSQL admitted 128 sessions/transactions on every available tested worker profile")


if __name__ == "__main__":
    main()
