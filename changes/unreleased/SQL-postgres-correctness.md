- Preserve PostgreSQL result type modifiers in prepared descriptions and report
  the VARCHAR type for information_schema.columns.data_type.
- Keep a doomed Serializable writer from aborting its surviving peer. The
  write-skew final rows match PostgreSQL, including after durable restart.
  The stage at which the rejected writer receives SQLSTATE 40001 still differs.
