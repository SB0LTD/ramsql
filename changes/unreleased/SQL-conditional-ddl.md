Adds conditional CREATE/DROP for supported tables and sequences, preserving
existing data/metadata and skipping empty durability callbacks/records. Pending
sequence watermarks still persist. Corrects sequence-DROP wait/retry snapshots
and retains ordinary IF identifiers and skipped semantic-invalid definitions.
Adds native-reference, concurrency, WAL replay and durable server tests.
