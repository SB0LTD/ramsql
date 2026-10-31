Adds PostgreSQL-style plain JSON extraction and JSONB concatenation in shared
ZPM core, including unknown-operand typing, lexical JSON preservation and scalar
path behavior. Canonicalization uses a single bounded key pool and heapsort;
input and new operators participate in statement work control. Adds core and live
PG16/18 parameter/binary/durable-restart regression coverage. Existing cell/path
limits and remaining JSON/SQL inventory gaps are documented.
