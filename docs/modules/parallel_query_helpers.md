# Parallel query helpers

`--query-helpers N` enables an experimental team of one to three persistent
native helpers for PostgreSQL autocommit reads. The default is zero. The team
requires a build with compact integral reads, native worker support and at
least two configured PostgreSQL reactors. It is unavailable in database
cluster mode and with MySQL or TDS listeners.

Startup reserves the helper stack grant and verifies the observed CPU allowance
can cover the reactors and helpers. Automatic reactor sizing accounts for the
helper count and memory grant. `--machine-capacity` reports the extra
reservation. Explicit helper requests fail before listening when the host
cannot admit the requested team.

Eligible dense integral GROUP BY plans use published column slices under the
same immutable read gate as other detached reads. Each dispatch joins its
helper tasks before releasing the slices. When another statement owns the
helper team, the requester runs its tasks inline. The reactor handles client
cancel and deadline checks between tiles. Shutdown joins the reactors before
stopping the helper team and releasing its grant.
