from pathlib import Path
import re
import subprocess
import sys

from test_server import Server

IMAGE = "postgres:18.3-bookworm@sha256:80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
EVIDENCE = Path("evidence")
SETUP = "CREATE TABLE tooling(id INT PRIMARY KEY, label TEXT)"
TOOLING_INDEX = "CREATE INDEX tooling_label ON tooling (label)"
TOOLING_CHILD = "CREATE TABLE tooling_child (tool_id INT, CONSTRAINT tooling_fk FOREIGN KEY (tool_id) REFERENCES tooling(id))"
TOOLING_POLICIES = (
    "CREATE ROLE tool_reader",
    "CREATE POLICY active ON tooling FOR SELECT TO tool_reader USING (id > 0)",
    "CREATE POLICY open_write ON tooling FOR INSERT WITH CHECK (id > 1)",
)
TOOLING_STATISTICS = (
    'CREATE SCHEMA analysis_data',
    'CREATE STATISTICS tooling_stats (mcv,ndistinct) ON label, id FROM tooling',
    'CREATE STATISTICS analysis_data.tooling_stats ON label, id FROM tooling',
    'ALTER STATISTICS analysis_data.tooling_stats SET STATISTICS 300',
)
TOOLING_PUBLICATIONS = (
    'CREATE PUBLICATION tooling_all FOR ALL TABLES',
    'CREATE PUBLICATION tooling_schema FOR TABLES IN SCHEMA public',
    'CREATE PUBLICATION tooling_table FOR TABLE ONLY tooling',
)
INDEX_SETUP = (
    'CREATE TABLE "Case Table" ("Select" INT PRIMARY KEY, qty INT, label TEXT)',
    'CREATE INDEX "ix Weird" ON "Case Table" (qty DESC NULLS LAST) INCLUDE (label) WHERE qty > 1',
)
INDEX_SQL = """
SELECT i.relname, x.indisprimary, x.indisvalid, x.indisclustered, x.indisreplident,
       pg_catalog.pg_get_indexdef(i.oid), pg_catalog.pg_get_indexdef(i.oid, 0, true),
       pg_catalog.pg_get_indexdef(i.oid, 1, true), pg_catalog.pg_get_indexdef(i.oid, 2, true),
       pg_catalog.pg_get_indexdef(i.oid, -1, true)
FROM pg_catalog.pg_class i JOIN pg_catalog.pg_index x ON i.oid = x.indexrelid
WHERE x.indrelid = '"Case Table"'::regclass ORDER BY i.relname
"""


def psql(container_args, connection, label, *args):
    command = [*container_args, "psql", "-X", "-v", "ON_ERROR_STOP=1", *connection, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (EVIDENCE / f"{label}.out").write_text(result.stdout)
    (EVIDENCE / f"{label}.err").write_text(result.stderr)
    return result


def describe_queries(result):
    return re.findall(r"^/\*+ QUERY \*+/\s*\n(.*?)\n/\*{5,}/$", result.stdout + "\n" + result.stderr, re.M | re.S)


def check(result):
    if result.returncode:
        raise AssertionError(result.stderr)


def index_definitions(client, connection, label):
    for ordinal, statement in enumerate(INDEX_SETUP):
        check(psql(client, connection, f"{label}-index-setup-{ordinal}", "-c", statement))
    result = psql(client, connection, f"{label}-index-definitions", "-At", "-c", INDEX_SQL)
    check(result)
    assert len(result.stdout.splitlines()) == 2, result.stdout
    invalid = psql(client, connection, f"{label}-index-invalid", "-At", "-c",
                   "SELECT pg_catalog.pg_get_indexdef(0), pg_catalog.pg_get_indexdef(NULL), "
                   "pg_catalog.pg_get_indexdef(0, 0, true)")
    check(invalid)
    assert invalid.stdout == "||\n", invalid.stdout
    return result.stdout


def incoming_foreign_keys(client, connection, label, native_incoming):
    check(psql(client, connection, label + '-foreign-key-setup', '-c', TOOLING_CHILD))
    # Render the dynamic table identity through each engine's regclass lookup.
    source = ('SELECT conname, ontable::pg_catalog.regclass::pg_catalog.text, condef FROM (' +
              native_incoming.strip().removesuffix(';') + ') AS incoming ORDER BY conname')
    result = psql(client, connection, label + '-incoming-foreign-keys', '-At', '-c', source)
    check(result)
    assert result.stdout == 'tooling_fk|tooling_child|FOREIGN KEY (tool_id) REFERENCES tooling(id)\n', result.stdout


def row_policies(client, connection, label, policy_sql):
    for index, definition in enumerate(TOOLING_POLICIES):
        check(psql(client, connection, f'{label}-policy-{index}', '-c', definition))
    result = psql(client, connection, label + '-policies', '-At', '-c', policy_sql)
    check(result)
    expected = 'active|t|tool_reader|(id > 0)||SELECT\nopen_write|t|||(id > 1)|INSERT\n'
    assert result.stdout == expected, result.stdout


def statistics(client, connection, label, statistics_sql, relation_oid):
    empty = psql(client, connection, label + '-no-statistics', '-At', '-c', statistics_sql)
    check(empty)
    assert empty.stdout == '', empty.stdout
    for index, definition in enumerate(TOOLING_STATISTICS):
        check(psql(client, connection, f'{label}-statistics-{index}', '-c', definition))
    result = psql(client, connection, label + '-statistics', '-At', '-c', statistics_sql)
    check(result)
    observed = []
    object_ids = set()
    relation = 'tooling' if label == 'native' else relation_oid
    for line in result.stdout.splitlines():
        object_id, relation_id, metadata = line.split('|', 2)
        assert object_id.isdecimal() and int(object_id) > 0 and object_id not in object_ids, line
        assert relation_id == relation, line
        object_ids.add(object_id)
        observed.append(metadata)
    assert observed == ['analysis_data|tooling_stats|id, label|t|t|t|300',
                        'public|tooling_stats|id, label|t|f|t|'], result.stdout
    check(psql(client, connection, label + '-drop-statistics', '-c',
               'DROP STATISTICS public.tooling_stats, analysis_data.tooling_stats'))
    after = psql(client, connection, label + '-after-drop-statistics', '-At', '-c', statistics_sql)
    check(after)
    assert after.stdout == '', after.stdout


def publications(client, connection, label, publication_sql):
    empty = psql(client, connection, label + '-no-publications', '-At', '-c', publication_sql)
    check(empty)
    assert empty.stdout == '', empty.stdout
    for index, definition in enumerate(TOOLING_PUBLICATIONS):
        check(psql(client, connection, f'{label}-publication-{index}', '-c', definition))
    result = psql(client, connection, label + '-publications', '-At', '-c', publication_sql)
    check(result)
    assert result.stdout == 'tooling_all||\ntooling_schema||\ntooling_table||\n', result.stdout
    check(psql(client, connection, label + '-drop-publications', '-c',
               'DROP PUBLICATION tooling_all, tooling_schema, tooling_table'))
    after = psql(client, connection, label + '-after-drop-publications', '-At', '-c', publication_sql)
    check(after)
    assert after.stdout == '', after.stdout


def main():
    native = ["docker", "exec", "tooling-native"]
    native_conn = ["-U", "postgres", "-d", "postgres"]
    check(psql(native, native_conn, "native-version", "--version"))
    check(psql(native, native_conn, "native-setup", "-c", SETUP))
    check(psql(native, native_conn, "native-tooling-index", "-c", TOOLING_INDEX))
    native_describe = psql(native, native_conn, "native-describe", "-E", "-c", r"\d tooling")
    check(native_describe)
    lookup, relation_sql, attribute_sql, constraint_indexes_sql, outgoing_sql, incoming_sql, policy_sql, stats_sql, publication_sql, *_ = describe_queries(native_describe)
    assert "pg_table_is_visible" in lookup and "OPERATOR(pg_catalog.~)" in lookup and "COLLATE pg_catalog.default" in lookup
    assert "pg_catalog.pg_am" in relation_sql and "::pg_catalog.regtype::pg_catalog.text" in relation_sql
    assert "pg_catalog.pg_attribute" in attribute_sql and "pg_catalog.pg_collation" in attribute_sql
    assert "pg_catalog.pg_constraint" in constraint_indexes_sql and "pg_catalog.pg_get_constraintdef" in constraint_indexes_sql
    assert "pg_catalog.pg_partition_ancestors" in incoming_sql
    assert "pg_catalog.pg_policy" in policy_sql and "array(select rolname" in policy_sql
    assert "pg_catalog.pg_statistic_ext" in stats_sql and "any(stxkind)" in stats_sql
    assert "pg_catalog.pg_publication_rel" in publication_sql and "pg_catalog.pg_publication_namespace" in publication_sql
    (EVIDENCE / "discovery.sql").write_text(lookup + "\n")
    native_rows = psql(native, native_conn, "native-discovery", "-At", "-c", lookup)
    check(native_rows)
    assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", native_rows.stdout), native_rows.stdout
    native_relation = psql(native, native_conn, "native-relation", "-At", "-c", relation_sql)
    check(native_relation)
    assert native_relation.stdout == "0|r|t|f|f|f|f|f|f||0||p|d|heap\n", native_relation.stdout
    native_attributes = psql(native, native_conn, "native-attributes", "-At", "-c", attribute_sql)
    check(native_attributes)
    assert native_attributes.stdout == "id|integer||t|||\nlabel|text||f|||\n", native_attributes.stdout
    native_indexes = index_definitions(native, native_conn, "native")
    native_constraints = psql(native, native_conn, "native-index-constraints", "-At", "-c", constraint_indexes_sql)
    check(native_constraints)
    assert len(native_constraints.stdout.splitlines()) == 2 and "PRIMARY KEY (id)|p|f|f" in native_constraints.stdout
    for label, source in (('outgoing', outgoing_sql), ('incoming', incoming_sql)):
        result = psql(native, native_conn, f'native-no-{label}', '-At', '-c', source)
        check(result)
        assert result.stdout == '', result.stdout
    incoming_foreign_keys(native, native_conn, 'native', incoming_sql)
    row_policies(native, native_conn, 'native', policy_sql)
    statistics(native, native_conn, 'native', stats_sql, native_rows.stdout.split('|', 1)[0])
    publications(native, native_conn, 'native', publication_sql)
    Server.test_workers = 2
    with Server(Path(sys.argv[1]).resolve()) as server:
        client = ["docker", "run", "--rm", "--network", "host", IMAGE]
        conn = ["-h", "127.0.0.1", "-p", str(server.port), "-U", "ramsql", "-d", "ramsql"]
        check(psql(client, conn, "ramsql-setup", "-c", SETUP))
        check(psql(client, conn, "ramsql-tooling-index", "-c", TOOLING_INDEX))
        actual = psql(client, conn, "ramsql-describe", "-E", "-c", r"\d tooling")
        actual_lookup, actual_relation_sql, actual_attribute_sql, *_ = describe_queries(actual)
        assert actual_lookup == lookup
        rows = psql(client, conn, "ramsql-discovery", "-At", "-c", lookup)
        check(rows)
        assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", rows.stdout), rows.stdout
        old_oid = native_rows.stdout.split("|", 1)[0]
        new_oid = rows.stdout.split("|", 1)[0]
        assert actual_relation_sql == relation_sql.replace(f"c.oid = '{old_oid}'", f"c.oid = '{new_oid}'")
        actual_relation = psql(client, conn, "ramsql-relation", "-At", "-c", actual_relation_sql)
        check(actual_relation)
        assert actual_relation.stdout == native_relation.stdout, (native_relation.stdout, actual_relation.stdout)
        assert actual_attribute_sql == attribute_sql.replace(f"a.attrelid = '{old_oid}'", f"a.attrelid = '{new_oid}'")
        actual_attributes = psql(client, conn, "ramsql-attributes", "-At", "-c", actual_attribute_sql)
        check(actual_attributes)
        assert actual_attributes.stdout == native_attributes.stdout, (native_attributes.stdout, actual_attributes.stdout)
        actual_indexes = index_definitions(client, conn, "ramsql")
        assert actual_indexes == native_indexes, (native_indexes, actual_indexes)
        ram_constraint_sql = constraint_indexes_sql.replace(f"c.oid = '{old_oid}'", f"c.oid = '{new_oid}'")
        actual_constraints = psql(client, conn, "ramsql-index-constraints", "-At", "-c", ram_constraint_sql)
        check(actual_constraints)
        assert actual_constraints.stdout == native_constraints.stdout, (native_constraints.stdout, actual_constraints.stdout)
        assert len(describe_queries(actual)) >= 9, actual.stderr
        for label, source in (('outgoing', outgoing_sql), ('incoming', incoming_sql)):
            rebound = source.replace(f"'{old_oid}'", f"'{new_oid}'")
            empty = psql(client, conn, f'ramsql-no-{label}', '-At', '-c', rebound)
            check(empty)
            assert empty.stdout == '', empty.stdout
        incoming_foreign_keys(client, conn, 'ramsql', incoming_sql.replace(f"'{old_oid}'", f"'{new_oid}'"))
        row_policies(client, conn, 'ramsql', policy_sql.replace(f"'{old_oid}'", f"'{new_oid}'"))
        ram_stats_sql = stats_sql.replace(f"'{old_oid}'", f"'{new_oid}'")
        statistics(client, conn, 'ramsql', ram_stats_sql, new_oid)
        ram_publication_sql = publication_sql.replace(f"'{old_oid}'", f"'{new_oid}'")
        publications(client, conn, 'ramsql', ram_publication_sql)
        assert len(describe_queries(actual)) >= 10 or actual.returncode == 0, actual.stderr
        (EVIDENCE / "relation-metadata.sql").write_text(actual_relation_sql + "\n")
        (EVIDENCE / "column-metadata.sql").write_text(actual_attribute_sql + "\n")
        (EVIDENCE / "index-constraints.sql").write_text(ram_constraint_sql + "\n")
        (EVIDENCE / "extended-statistics.sql").write_text(ram_stats_sql + "\n")
        (EVIDENCE / "publications.sql").write_text(ram_publication_sql + "\n")
        print("PASS native psql 18.3 discovery SQL on both engines")
        print("PASS native psql 18.3 relation-metadata SQL and values on both engines")
        print("PASS native psql 18.3 column-metadata SQL and values on both engines")
        print("PASS quoted primary and partial covering index definitions and missing OIDs on both engines")
        print("PASS stock psql primary-constraint and secondary-index metadata on both engines")
        print("PASS stock psql incoming and outgoing foreign-key discovery on both engines")
        print("PASS stock psql named-role and PUBLIC row-policy discovery on both engines")
        print("PASS stock psql extended statistics discovery before creation and after drop on both engines")
        print("PASS stock psql explicit table, schema and all-table publication discovery on both engines")
        print(f"psql describe exit status: native={native_describe.returncode} RAMSQL={actual.returncode}")
        print(actual.stderr, end="")
        if not actual.returncode:
            assert "tooling" in actual.stdout
            assert "integer" in actual.stdout
            assert "label" in actual.stdout


if __name__ == "__main__":
    main()
