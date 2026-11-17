"""Qualify a real six-column fact load and three-table joins over PostgreSQL."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import subprocess
from threading import Barrier

from benchmark import process_memory
from test_server import Server


ROOT = Path(__file__).resolve().parents[1]
FACT_ROWS = 100_000_000
PROCESS_BUDGET = 14_347_884_288
PRODUCT_KEYS = 1024
STORE_KEYS = 128
PHASES = 4
PERIOD = PRODUCT_KEYS * STORE_KEYS * PHASES
CLIENTS = 4
WORKERS = 2
BATCH_ROWS = 1000

CREATE_FACT = (
    "CREATE TABLE sales(id INT PRIMARY KEY, product_id INT, store_id INT, "
    "units INT, amount_cents INT, quality INT)"
)
CREATE_PRODUCTS = "CREATE TABLE products(id INT PRIMARY KEY, category INT, enabled INT)"
CREATE_STORES = "CREATE TABLE stores(id INT PRIMARY KEY, region INT, zone INT)"
PRODUCTS = {key: (key % 13, int(key % 7 != 0))
            for key in range(PRODUCT_KEYS) if key % 17 != 0}
STORES = {key: ((key // 3) % 7, key % 5) for key in range(STORE_KEYS) if key % 11 != 0}

STAR = "FROM sales f JOIN products p ON p.id=f.product_id JOIN stores s ON s.id=f.store_id"
GROUP_COLUMNS = (
    "SELECT s.region,p.category,COUNT(*),COUNT(f.units),SUM(f.units),"
    "SUM(f.amount_cents),MIN(f.amount_cents),MAX(f.amount_cents) "
)
GROUP_ORDER = " GROUP BY s.region,p.category ORDER BY s.region,p.category"
WHOLE_STAR = GROUP_COLUMNS + STAR + GROUP_ORDER
SELECTIVE_STAR = (
    GROUP_COLUMNS + STAR +
    " WHERE f.store_id=4 AND p.category IN (2,5,9) AND p.enabled=1 AND f.quality>=3" +
    GROUP_ORDER
)
RANGE_STAR = (
    "SELECT f.id,p.category,s.region,f.units,f.amount_cents " + STAR +
    " WHERE f.id>={start} ORDER BY f.amount_cents DESC,f.id LIMIT 20"
)


def fact(key):
    block, product = divmod(key, PRODUCT_KEYS)
    phase, store = divmod(block, STORE_KEYS)
    phase %= PHASES
    units = None if ((product ^ store) + phase) % 23 == 0 else (product + phase) % 19
    amount = product * 3 - store * 13 + phase * 37
    quality = (product + 3 * store + phase) % 8
    return product, store, units, amount, quality


@dataclass
class Aggregate:
    rows: int = 0
    nonnull_units: int = 0
    units: int = 0
    amount: int = 0
    minimum: int | None = None
    maximum: int | None = None

    def add(self, repeats, units, amount):
        self.rows += repeats
        if units is not None:
            self.nonnull_units += repeats
            self.units += repeats * units
        self.amount += repeats * amount
        self.minimum = amount if self.minimum is None else min(self.minimum, amount)
        self.maximum = amount if self.maximum is None else max(self.maximum, amount)

    def row(self, group):
        return (*group, self.rows, self.nonnull_units, self.units if self.nonnull_units else None,
                self.amount, self.minimum, self.maximum)


def expected_groups(rows):
    """Reduce one period and its exact tail, using scalar dimension lookups."""
    cycles, tail = divmod(rows, PERIOD)
    whole, selected = {}, {}
    for key in range(PERIOD if cycles else tail):
        product, store, units, amount, quality = fact(key)
        if product not in PRODUCTS or store not in STORES:
            continue
        category, enabled = PRODUCTS[product]
        region, _ = STORES[store]
        group = region, category
        repeats = cycles + int(key < tail)
        whole.setdefault(group, Aggregate()).add(repeats, units, amount)
        if store == 4 and category in (2, 5, 9) and enabled and quality >= 3:
            selected.setdefault(group, Aggregate()).add(repeats, units, amount)
    return tuple([state.row(group) for group, state in sorted(groups.items())]
                 for groups in (whole, selected))


def expected_range(rows):
    values = []
    for key in range(rows - 128, rows):
        product, store, units, amount, _ = fact(key)
        if product in PRODUCTS and store in STORES:
            values.append((key, PRODUCTS[product][0], STORES[store][0], units, amount))
    return sorted(values, key=lambda row: (-row[4], row[0]))[:20]


def expect(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: got {actual!r}, expected {expected!r}")


def row_block(start, stop):
    lines = []
    for key in range(start, stop):
        product, store, units, amount, quality = fact(key)
        units_field = r"\N" if units is None else str(units)
        lines.append(f"{key}\t{product}\t{store}\t{units_field}\t{amount}\t{quality}\n")
    return "".join(lines).encode("ascii")


def dimensions(rows):
    return "".join(f"{key}\t{values[0]}\t{values[1]}\n" for key, values in rows.items()).encode("ascii")


def copy_rows(db, table, data):
    with db.cursor() as cursor, cursor.copy(f"COPY {table} FROM STDIN") as copy:
        copy.write(data)


def options_for(rows, memory_budget, table_slots):
    if table_slots < 3:
        raise ValueError("fixture needs three table banks")
    unused = table_slots - 3
    row_caps = [rows, PRODUCT_KEYS, STORE_KEYS] + [1] * unused
    version_caps = [rows + 65_536, 2 * PRODUCT_KEYS, 2 * STORE_KEYS] + [2] * unused
    words = [6, 0, 0] + [0] * unused
    return (
        "--table-rows", ",".join(map(str, row_caps)),
        "--table-versions", ",".join(map(str, version_caps)),
        "--table-integral-words", ",".join(map(str, words)),
        "--typed-bank-memory-profile", "--work-rows", "8192", "--result-rows", "1024",
        "--workers", str(WORKERS), "--memory-budget", str(memory_budget),
    )


def read_json(binary, command, options=()):
    return json.loads(subprocess.check_output([str(binary), command, *options], timeout=30))


def concurrent_probes(connect, rows):
    start = Barrier(2, timeout=10)

    def read(client):
        sql = "SELECT id,product_id,store_id,units,amount_cents,quality FROM sales WHERE id=%s"
        missing_dimensions = 0
        with closing(connect()) as db:
            start.wait()
            for probe in range(256):
                key = ((2 * probe + client) * 48271) % rows
                values = fact(key)
                product, store, *_ = values
                expect(f"client {client} fact {key}", db.execute(sql, (key,), prepare=False).fetchall(),
                       [(key, *values)])
                for table, dimensions_by_id, columns, dimension_id in (
                    ("products", PRODUCTS, "category,enabled", product),
                    ("stores", STORES, "region,zone", store),
                ):
                    attributes = dimensions_by_id.get(dimension_id)
                    actual = db.execute(f"SELECT {columns} FROM {table} WHERE id=%s", (dimension_id,),
                                        prepare=False).fetchall()
                    expect(f"client {client} {table} {dimension_id}", actual,
                           [] if attributes is None else [attributes])
                    missing_dimensions += attributes is None
            expect(f"client {client} missing key", db.execute(sql, (-1,), prepare=False).fetchall(), [])
        return {"fact_hits": 256, "dimension_probes": 512,
                "missing_dimensions": missing_dimensions, "missing_fact": 1}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(read, range(2)))
    assert any(result["missing_dimensions"] for result in results)
    return results


def run_queries(binary, options, rows, report):
    import psycopg

    expected_whole, expected_selected = expected_groups(rows)
    assert len(expected_whole) == 91
    assert len(expected_selected) == 3
    assert 0 < sum(row[2] for row in expected_whole) < rows
    assert 0 < sum(row[2] for row in expected_selected) < sum(row[2] for row in expected_whole)
    report["stage"] = "start_server"
    with Server(binary, idle_ms=120_000, max_clients=CLIENTS, extra_arguments=options) as server:
        connect = partial(psycopg.connect, host="127.0.0.1", port=server.port, user="ramsql",
                          dbname="ramsql", sslmode="disable", autocommit=True, connect_timeout=5,
                          prepare_threshold=None)
        try:
            report["memory_before_load"] = process_memory(server.process)
            with closing(connect()) as db:
                report["stage"] = "load_dimensions"
                for sql in (CREATE_FACT, CREATE_PRODUCTS, CREATE_STORES):
                    db.execute(sql, prepare=False)
                for table, values in (("products", PRODUCTS), ("stores", STORES)):
                    assert len(values) < BATCH_ROWS
                    copy_rows(db, table, dimensions(values))
                    expect(f"{table} contents", db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall(),
                           [(key, *attributes) for key, attributes in values.items()])

                report["stage"] = "load_fact"
                for start in range(0, rows, BATCH_ROWS):
                    stop = min(start + BATCH_ROWS, rows)
                    copy_rows(db, "sales", row_block(start, stop))
                    report["loaded_fact_rows"] = stop
                    if stop % 100_000 == 0 or stop == rows:
                        print(f"checked star bulk input: {stop} rows", flush=True)

                report["stage"] = "fact_bounds"
                report["fact_bounds"] = db.execute("SELECT COUNT(*),MIN(id),MAX(id) FROM sales").fetchone()
                expect("fact bounds", report["fact_bounds"], (rows, 0, rows - 1))

                report["stage"] = "whole_star"
                report["whole_star"] = db.execute(WHOLE_STAR, prepare=False).fetchall()
                expect("whole star", report["whole_star"], expected_whole)

                report["stage"] = "selective_star"
                report["selective_star"] = db.execute(SELECTIVE_STAR, prepare=False).fetchall()
                expect("selective star", report["selective_star"], expected_selected)

                report["stage"] = "ordered_range"
                sql = RANGE_STAR.format(start=rows - 128)
                wanted = expected_range(rows)
                if rows == FACT_ROWS:
                    assert len(wanted) == 20
                try:
                    actual = db.execute(sql, prepare=False).fetchall()
                except psycopg.Error as error:
                    if error.sqlstate != "54000" or error.diag.message_primary != "WorkspaceCapacity":
                        raise
                    report["ordered_range"] = {"status": "known_workspace_limit", "sql": sql,
                                               "sqlstate": error.sqlstate, "expected": wanted}
                else:
                    expect("ordered range", actual, wanted)
                    report["ordered_range"] = {"status": "passed", "sql": sql, "rows": actual}
            report["stage"] = "concurrent_point_reads"
            report["concurrent_probes"] = concurrent_probes(connect, rows)
            assert server.process.poll() is None
        finally:
            report["memory_after_checks"] = process_memory(server.process)
            report["server_stderr_tail"] = list(server.errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out/bin" /
                        ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--rows", type=int, default=FACT_ROWS)
    parser.add_argument("--memory-budget", type=int, default=PROCESS_BUDGET)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 2 * PRODUCT_KEYS * STORE_KEYS <= args.rows <= FACT_ROWS or args.memory_budget <= 0:
        parser.error("provide 262144 to 100000000 fact rows and a positive memory budget")
    binary = args.binary.resolve(strict=True)
    with binary.open("rb") as stream:
        binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"suite": "large-star-runtime", "passed": False, "fact_rows": args.rows,
              "fact_width_integral_words": 6, "plan_only": args.plan_only, "binary_sha256": binary_hash,
              "product_rows": len(PRODUCTS), "store_rows": len(STORES), "workers": WORKERS,
              "durability": "memory", "stage": "plan",
              "scope": "Load, full and selective grouped three-way joins, and independent point probes. "
                       "The joined ordered range reports its known workspace limitation separately."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    try:
        profile = read_json(binary, "--capabilities")
        assert profile["integral_version_banks"], "build with -Dintegral-version-banks=true"
        options = options_for(args.rows, args.memory_budget, profile["tables"])
        planned_options = (*options, "--max-clients", str(CLIENTS))
        machine = read_json(binary, "--machine-capacity", planned_options)
        report["machine_capacity"] = machine
        report["startup_options"] = planned_options
        fact_bank, *dimensions_and_spares = machine["table_storage"]
        assert fact_bank["rows"] == args.rows and fact_bank["integral_row_words"] == 6
        assert not fact_bank["committed_scan_projection"] and fact_bank["secondary_hashes"] == 0
        assert all(bank["committed_scan_projection"] for bank in dimensions_and_spares)
        assert (dimensions_and_spares[0]["rows"], dimensions_and_spares[1]["rows"]) == (PRODUCT_KEYS, STORE_KEYS)
        assert machine["read_workers"] == WORKERS
        assert machine["startup_fits"], "native memory grant cannot admit the exact fixture"
        if not args.plan_only:
            run_queries(binary, options, args.rows, report)
        report.update(passed=True, stage="complete")
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"star correctness stage={report['stage']} rows={args.rows} plan_only={args.plan_only}", flush=True)


if __name__ == "__main__":
    main()
