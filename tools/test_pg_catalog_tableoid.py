"""Compare catalog tableoid and the stock extension query with PostgreSQL."""

import argparse
import json
from pathlib import Path
import struct

from test_pg_idle_transaction import connect, scalar, sql
from test_server import Server


STOCK_EXTENSION_SQL = (
    "SELECT x.tableoid, x.oid, x.extname, n.nspname, x.extrelocatable, "
    "x.extversion, x.extconfig, x.extcondition FROM pg_extension x JOIN "
    "pg_namespace n ON n.oid = x.extnamespace"
)


def row(pg, source):
    reply = sql(pg, source)
    descriptor, = (body for tag, body in reply if tag == b"T")
    payload, = (body for tag, body in reply if tag == b"D")
    width, = struct.unpack_from("!H", descriptor)
    assert struct.unpack_from("!H", payload) == (width,)
    fields = []
    values = []
    offset = data = 2
    for _ in range(width):
        end = descriptor.index(b"\0", offset)
        oid, = struct.unpack_from("!I", descriptor, end + 7)
        fields.append((descriptor[offset:end].decode(), oid))
        offset = end + 19
        size, = struct.unpack_from("!i", payload, data)
        data += 4
        if size == -1:
            values.append(None)
        else:
            assert size >= 0
            values.append(payload[data:data + size].decode())
            data += size
    assert offset == len(descriptor) and data == len(payload)
    return fields, values


def probe(port, user, database):
    with connect(port, user, database) as pg:
        assert scalar(pg, "SELECT pg_catalog.set_config('search_path', '', false)") == ""
        fields, values = row(pg, STOCK_EXTENSION_SQL)
        assert fields == [
            ("tableoid", 26), ("oid", 26), ("extname", 19), ("nspname", 19),
            ("extrelocatable", 16), ("extversion", 25), ("extconfig", 1028), ("extcondition", 1009),
        ]
        catalog_oid, extension_oid, *extension = values
        assert catalog_oid == "3079" and int(extension_oid) > 0
        assert extension == ["plpgsql", "pg_catalog", "f", "1.0", None, None]
        assert scalar(pg, "SELECT 'pg_catalog.pg_extension'::regclass::oid") == catalog_oid
        assert scalar(pg, "SELECT (SELECT x.tableoid) FROM pg_extension x") == catalog_oid
        assert scalar(pg, "SELECT x.tableoid FROM pg_extension AS x(extension_id)") == catalog_oid
        assert row(pg, "SELECT n.tableoid, x.tableoid FROM pg_namespace n LEFT JOIN pg_extension x ON x.extnamespace=n.oid WHERE n.nspname='public'")[1] == ["2615", None]
        assert row(pg, "SELECT tableoid, count(*) FROM pg_extension GROUP BY tableoid ORDER BY tableoid")[1] == [catalog_oid, "1"]
        assert scalar(pg, "SELECT tableoid FROM pg_extension WHERE tableoid=3079::oid") == catalog_oid
        assert row(pg, "SELECT tableoid, count(tableoid) OVER () FROM pg_extension")[1] == [catalog_oid, "1"]
        assert [name for name, _ in row(pg, "SELECT x.* FROM pg_extension x")[0]] == [
            "oid", "extname", "extowner", "extnamespace", "extrelocatable", "extversion", "extconfig", "extcondition",
        ]
        sql(pg, "SELECT tableoid FROM pg_extension x CROSS JOIN pg_namespace n", error=b"42703")
        sql(pg, "SELECT tableoid FROM pg_extension x, pg_namespace n", error=b"42702")
        assert scalar(pg, "SELECT x.tableoid FROM pg_extension x JOIN (SELECT 1) q ON tableoid = 3079::oid") == catalog_oid
        assert scalar(pg, "SELECT tableoid FROM pg_extension x, (SELECT 1) a CROSS JOIN (SELECT 2) b") == catalog_oid
        sql(pg, "SELECT tableoid, count(*) FROM pg_extension", error=b"42803")
        sql(pg, "SELECT tableoid FROM pg_settings", error=b"42703")
        sql(pg, "SELECT v.tableoid FROM (SELECT oid FROM pg_extension) v", error=b"42703")
    return {"catalog_oid": catalog_oid, "extension": extension, "outer_join": ["2615", None]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference-port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    native = probe(args.reference_port, "postgres", "postgres")
    candidates = []
    for workers in [1, 4]:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=4) as server:
            result = probe(server.port, "ramsql", "ramsql")
        assert result == native
        candidates.append({"workers": workers, "observed": result})
    report = {"pass": True, "native": native, "ram_sql": candidates}
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
