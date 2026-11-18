"""Compare the PostgreSQL non-system relation access guard on live sessions."""

import argparse
import json
from pathlib import Path

from test_pg_idle_transaction import connect, scalar, sql
from test_server import Server


SETTING = "restrict_nonsystem_relation_kind"
VIEW_SCAN = "SELECT id FROM public.restriction_view"


def probe(port, user, database):
    def client(options=None):
        return connect(port, user, database, options)

    show = f"SHOW {SETTING}"
    with client() as pg, client() as other:
        assert scalar(pg, show) == scalar(other, show) == ""
        sql(pg, "CREATE TABLE restriction_base (id INT PRIMARY KEY)")
        sql(pg, "INSERT INTO restriction_base VALUES (31)")
        sql(pg, "CREATE VIEW restriction_view AS SELECT id FROM restriction_base")
        sql(pg, "CREATE MATERIALIZED VIEW restriction_mat AS SELECT id FROM restriction_base")
        assert scalar(pg, VIEW_SCAN) == "31"
        sql(pg, "BEGIN", b"T")
        sql(pg, f"SET LOCAL {SETTING} = 'VIEW, foreign-table'", b"T")
        assert scalar(pg, show, b"T") == "VIEW, foreign-table"
        assert scalar(other, VIEW_SCAN) == "31"
        sql(pg, VIEW_SCAN, b"E", error=b"55000")
        sql(pg, "ROLLBACK")
        assert scalar(pg, VIEW_SCAN) == "31"
        assert scalar(pg, f"SELECT set_config('{SETTING}', 'view, foreign-table', false)") == "view, foreign-table"
        sql(pg, f"SET {SETTING} = 'view, bogus'", error=b"22023")
        sql(pg, VIEW_SCAN, error=b"55000")
        sql(pg, "EXPLAIN " + VIEW_SCAN, error=b"55000")
        sql(pg, "INSERT INTO restriction_view VALUES (32)", error=b"55000")
        sql(pg, "UPDATE restriction_view SET id=32", error=b"55000")
        sql(pg, "PREPARE limited AS " + VIEW_SCAN, error=b"55000")
        sql(pg, f"COPY ({VIEW_SCAN}) TO STDOUT", error=b"55000")
        assert scalar(pg, "SELECT id FROM restriction_base") == "31"
        assert scalar(pg, "SELECT id FROM restriction_mat") == "31"
        assert scalar(pg, "SELECT count(*) FROM pg_catalog.pg_views WHERE viewname='restriction_view'") == "1"
        sql(pg, "CREATE VIEW restriction_chain AS SELECT id FROM restriction_view")
        sql(pg, "CREATE MATERIALIZED VIEW restriction_nodata AS SELECT id FROM restriction_view WITH NO DATA")
        sql(pg, "REFRESH MATERIALIZED VIEW restriction_nodata", error=b"55000")
        sql(pg, "SELECT id FROM restriction_chain", error=b"55000")
        sql(pg, f"SET {SETTING} TO view, 'foreign-table'")
        assert scalar(pg, show) == "view, foreign-table"
        sql(pg, f"SET {SETTING} = 'foreign-table'")
        assert scalar(pg, VIEW_SCAN) == "31"
        sql(pg, f"RESET {SETTING}")
        assert scalar(pg, show) == ""
    with client(f"-c {SETTING}=view") as pg:
        sql(pg, VIEW_SCAN, error=b"55000")
        sql(pg, f"SET {SETTING} = ''")
        assert scalar(pg, VIEW_SCAN) == "31"
        sql(pg, f"RESET {SETTING}")
        sql(pg, VIEW_SCAN, error=b"55000")
    return {"view": "55000", "write": "55000", "materialized": "31", "system": "1", "startup_reset": "view"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--reference-port", type=int)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    native = probe(args.reference_port, "postgres", "postgres") if args.reference_port else None
    modes = []
    for workers in args.workers:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=8) as server:
            result = probe(server.port, "ramsql", "ramsql")
        if native is not None:
            assert result == native, (workers, result, native)
        modes.append({"workers": workers, "observed": result})
    report = {"pass": True, "postgres_reference": native, "ramsql": modes}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
