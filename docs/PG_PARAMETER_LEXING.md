# Shared PostgreSQL parameter lexing candidate

RAMSQL pins ZPM976d6081f89f0bbe863ad14dde544336d9bfa129 through its immutable
archive and verified SHA256adfd2cb2b2d94344bd619b37a744bf30e0375c4b12f64b82239fd50d8b096db8.
Reusable parameter scanning remains in ZPM L1 and uses the canonical L0 SQL
tokenizer. RAMSQL adds the explicit dependency to hosted and native SB0 build
commands, integration cases and documentation; it adds no runtime SQL parser.

The existing q01 native differential catalog now requires76 additional statements:
65 nested comment depths, quoted/comment/identifier dollars, text/binary results,
malformed templates and mutations whose actual targets must remain unchanged.
No memory/restart skips were added. The same catalog runs against PG16/18 in
memory and durable/SCRAM modes and on the exact Linux release artifact.

Local matched ReleaseSafe build and76 real-wire statements pass. Exact binary,
input and report hashes are in [the evidence manifest](../compatibility/pg-parameter-lexing-evidence.json).
ZPM's native oracle independently passes78 PREPARE cases on pinned PostgreSQL16.15
and18.3. Its fresh reviewer reproduced baseline failure and candidate success.
Product independent review and hosted release qualification remain pending.

Typed AST parameter binding, general OID0 inference, remaining NET findings,
SQL-family completion and production gates remain open. The TDD documents now
point to the latest recorded12/89/17 inventory and preserve historical rows;
this protocol repair does not close another SQL family. Automatic publication
will create a non-latest CI prerelease only after the complete release matrix.
