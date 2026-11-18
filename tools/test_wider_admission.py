"""Probe real schema placement and native 100M-row admission without loading the full plans."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess

from test_server import Server, connection, expect_error


ROWS = 100_000_000
HEADROOM_VERSIONS = 65_536
COLUMNS = 8
PROFILE = ("--work-rows", "8192", "--result-rows", "1024")


@dataclass(frozen=True)
class Schema:
    name: str
    ddl: str
    column_bytes: tuple[int, ...] | None
    sample: str
    first: tuple[str | None, ...]

    @property
    def words(self):
        if self.column_bytes is None:
            return 0
        bitmap = (len(self.column_bytes) + 7) // 8
        return (bitmap + sum(self.column_bytes) + 7) // 8


SCHEMAS = (
    Schema("narrow", "id INT PRIMARY KEY, units SMALLINT", (4, 2),
           "(1,5),(2,-3),(3,NULL)", ("1", "5")),
    Schema("six_int_fact", "id INT PRIMARY KEY, customer_id INT, product_id INT, units INT, amount_cents INT, state INT",
           (4,) * 6, "(1,8,23,6,799,1),(2,13,7,3,-9,0),(3,1,2,NULL,11,1)",
           ("1", "8", "23", "6", "799", "1")),
    Schema("ledger_int_date", "id BIGINT PRIMARY KEY, day DATE, customer_id INT, product_id INT, units SMALLINT, "
           "amount_minor BIGINT, tax_minor BIGINT, state SMALLINT", (8, 4, 4, 4, 2, 8, 8, 2),
           "(1,DATE '2000-01-02',8,23,6,799,47,1),(2,DATE '1999-12-31',13,7,-3,-9,-1,0),"
           "(3,NULL,1,2,1,11,0,1)", ("1", "2000-01-02", "8", "23", "6", "799", "47", "1")),
    Schema("eight_bigint", "id BIGINT PRIMARY KEY, a BIGINT, b BIGINT, c BIGINT, d BIGINT, e BIGINT, f BIGINT, g BIGINT",
           (8,) * 8, "(1,2,3,4,5,6,7,8),(2,-3,4,5,6,7,8,9),(3,NULL,5,6,7,8,9,10)",
           ("1", "2", "3", "4", "5", "6", "7", "8")),
    Schema("invoice_text_numeric", "id BIGINT PRIMARY KEY, account INT, memo TEXT, amount NUMERIC(12,2), day DATE",
           None, "(1,8,'paid',12.34,DATE '2000-01-02'),(2,3,'credit',-5.25,DATE '1999-12-31'),"
           "(3,1,NULL,1.00,NULL)", ("1", "8", "paid", "12.34", "2000-01-02")),
)


def probe(binary, action, options=()):
    return json.loads(subprocess.check_output([str(binary), action, *options], timeout=30))


def options_for(table_slots, rows, words, clients, workers, query_budget=PROFILE):
    remainder = table_slots - len(words)
    capacities = [rows] * len(words) + [1] * remainder
    version_capacity = rows + HEADROOM_VERSIONS if rows == ROWS else rows * 2
    versions = [version_capacity] * len(words) + [2] * remainder
    # Leave the unused final slot typed so dense-only plans can share the
    # lean query-reference policy. It cannot host any fixture's dense schema.
    widths = list(words) + [0] * (remainder - 1) + [1]
    return (
        "--table-rows", ",".join(map(str, capacities)),
        "--table-versions", ",".join(map(str, versions)),
        "--table-integral-words", ",".join(map(str, widths)),
        "--typed-bank-memory-profile", "--packed-hash-load-75", *query_budget,
        "--max-clients", str(clients), "--workers", str(workers),
    )


def buckets(capacity, percent):
    slots = 2
    while slots * percent // 100 < capacity:
        slots *= 2
    return slots


def check_shape(binary, slots, schema):
    cases = (schema.words, schema.words - 1) if schema.words > 1 else (schema.words,)
    results = []
    for words in cases:
        opts = options_for(slots, 64, (words,), 4, 1, ("--work-rows", "64", "--result-rows", "16"))
        with Server(binary, max_clients=4, extra_arguments=opts) as server, connection(server) as db:
            db.ok(f"CREATE TABLE shape ({schema.ddl})")
            insertion = "INSERT INTO shape VALUES " + schema.sample
            if words == schema.words:
                db.ok(insertion)
                db.ok("SELECT COUNT(*) FROM shape", [["3"]])
                db.ok("SELECT * FROM shape WHERE id=1", [list(schema.first)])
                db.ok("UPDATE shape SET id=7 WHERE id=2")
                db.ok("SELECT COUNT(*) FROM shape WHERE id=7", [["1"]])
                results.append({"words": words, "status": "exact_schema_and_mutation"})
            else:
                # The table moved into a one-row dense bank because the
                # deliberately undersized packed bank could not encode it.
                expect_error(db.query(insertion), "54000")
                db.ok("SELECT COUNT(*) FROM shape", [["0"]])
                results.append({"words": words, "status": "packed_bank_refused_dense_fallback_atomic"})
    return results


def capacity(binary, slots, schemas, clients, workers):
    words = tuple(schema.words for schema in schemas)
    args = options_for(slots, ROWS, words, clients, workers)
    machine = probe(binary, "--machine-capacity", args)
    assert (machine["packed_hash_load_percent"], machine["storage_version_bytes"],
            machine["row_slot_bytes"], machine["read_workers"], machine["transaction_slots"]) == (75, 32, 16, workers, 130)
    table_bounds = []
    versions = ROWS + HEADROOM_VERSIONS
    for schema, table in zip(schemas, machine["table_storage"]):
        width = schema.words
        assert (table["rows"], table["versions"], table["integral_row_words"]) == (ROWS, versions, width)
        assert table["committed_scan_projection"] == (width == 0)
        row_bytes = machine["row_slot_bytes"] * ROWS
        header_bytes = machine["storage_version_bytes"] * versions
        payload_bytes = (width * 8 if width else machine["dense_version_payload_bytes"]) * versions
        percent = 75 if width else 50
        hash_bytes = 4 * (buckets(ROWS, percent) + buckets(versions, percent) * (1 + table["secondary_hashes"]))
        scan_floor = ROWS * (8 * COLUMNS + 9) if table["committed_scan_projection"] else 0
        table_bounds.append({
            "schema": schema.name, "typed_words": width, "row_directory_bytes": row_bytes,
            "version_header_bytes": header_bytes, "version_payload_bytes": payload_bytes,
            "row_and_key_hash_bytes": hash_bytes, "projection_lower_bound_bytes": scan_floor,
            "bytes_before_optional_presence_and_alignment": row_bytes + header_bytes + payload_bytes + hash_bytes + scan_floor,
        })
    assert machine["backing_bytes"] >= sum(table["bytes_before_optional_presence_and_alignment"] for table in table_bounds)
    assert machine["active_admission_bytes"] > machine["startup_region_bytes"] >= machine["backing_bytes"]
    assert machine["active_admission_fits"] == (machine["active_admission_bytes"] <= machine["new_region_bytes"])
    return {
        "case": "+".join(schema.name for schema in schemas), "rows_per_table": ROWS, "clients": clients,
        "workers": workers, "sql_tables_opened": 0, "table_components": table_bounds,
        "flags": args, "machine_capacity": machine,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    caps = probe(binary, "--capabilities")
    slots = caps["tables"]
    assert slots == 8 and caps["columns"] == COLUMNS
    assert caps["clients"] == 16 and caps["compiled_clients"] == 128
    assert caps["compact_version_stamps"] and caps["integral_version_banks"]
    assert caps["packed_hash_load_percent"] == 50
    workers = 2 if caps["native_workers_available"] else 1
    with binary.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    result = {
        "suite": "wider_100m_admission", "passed": False, "binary_sha256": digest,
        "schemas": [asdict(schema) | {"words": schema.words} for schema in SCHEMAS],
        "shape_probes": {}, "plans": [],
    }
    try:
        for schema in SCHEMAS:
            result["shape_probes"][schema.name] = check_shape(binary, slots, schema)
        scenarios = [(schema,) * count for schema in SCHEMAS for count in (1, 2)]
        scenarios.append((SCHEMAS[2], SCHEMAS[4]))
        for case in scenarios:
            for clients in (4, 60, 128):
                record = capacity(binary, slots, case, clients, workers)
                result["plans"].append(record)
                measured = record["machine_capacity"]
                print(f"{record['case']} clients={clients} peak_bytes={measured['active_admission_bytes']} "
                      f"grant_bytes={measured['new_region_bytes']} fits={measured['active_admission_fits']}", flush=True)
        result["passed"] = True
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
