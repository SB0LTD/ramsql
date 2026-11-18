"""Compare actual per-session XML parsing on the PostgreSQL wire."""

import argparse
import json
from pathlib import Path

from test_pg_idle_transaction import connect, scalar, sql
from test_server import Server, packet, query_packet


def probe(port, user, db):
    def client(options=None):
        return connect(port,user,db,options)
    show = 'SHOW xmloption'
    fragment = "SELECT 'before<r/>after'::xml"
    with client() as pg, client() as witness:
        assert scalar(pg,show) == scalar(witness,show) == 'content'
        sql(pg,'SET xmloption = unrelated',error=b'22023')
        assert scalar(pg,fragment) == 'before<r/>after'
        sql(pg,'BEGIN',b'T')
        sql(pg,'SET LOCAL XML OPTION DOCUMENT',b'T')
        assert scalar(pg,"SELECT current_setting('xmloption')",b'T') == 'document'
        assert scalar(pg,"SELECT XMLPARSE(CONTENT 'before<r/>after')",b'T') == 'before<r/>after'
        sql(pg,fragment,b'E',error=b'2200M')
        sql(pg,'ROLLBACK')
        assert scalar(pg,show) == 'content'
        sql(pg,"SET XML OPTION DOCUMENT")
        assert scalar(pg,"SELECT '<r/>'::xml") == '<r/>'
        sql(pg,fragment,error=b'2200M')
        assert scalar(witness,fragment) == 'before<r/>after'
        sql(pg,'CREATE TABLE markup(payload xml)')
        pg.sock.sendall(query_packet('COPY markup FROM STDIN'))
        assert pg.message()[0] == b'G'
        pg.sock.sendall(packet(b'd',b'<a/><b/>\n') + packet(b'c'))
        msg=pg.ready()
        error, = (body for tag,body in msg if tag==b'E')
        assert b'C2200M\0' in error,msg
        assert scalar(pg,'SELECT count(*) FROM markup') == '0'
        sql(pg,"SET XML OPTION CONTENT")
        assert scalar(pg,fragment) == 'before<r/>after'
        sql(pg,"SELECT '<broken'::xml",error=b'2200N')
    with client('-c xmloption=document') as pg:
        assert scalar(pg,show)=='document'
        sql(pg,'SET xmloption=content')
        sql(pg,'RESET xmloption')
        assert scalar(pg,show)=='document'
    return {'text_content':'before<r/>after','document_error':'2200M','content_error':'2200N','copy_rollback':'0','startup_reset':'document'}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--reference-port", type=int)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    reference = probe(args.reference_port, "postgres", "postgres") if args.reference_port else None
    modes = []
    for workers in args.workers:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=8) as server:
            result = probe(server.port, "ramsql", "ramsql")
        if reference is not None:
            assert result == reference, (workers, result, reference)
        modes.append({"workers": workers, "observed": result})
    report = {"pass": True, "postgres_reference": reference, "ramsql": modes}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
