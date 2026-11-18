# Required PostgreSQL TLS preview

Configure both `--tls-cert-der PATH` and `--tls-key-raw PATH` with the normal SCRAM
verifier options. This selects a TLS-required PostgreSQL loopback listener with
one reactor. Worker auto-selection resolves to one in this mode. The certificate
is one DER-encoded P-256 server leaf, at most 16 KiB. The key file contains the
matching 32-byte P-256 private scalar. Keep the scalar file accessible only to
the server user. The sample proof generates an ephemeral CA and instrumented
leaf outside the uploaded evidence directory.

The listener accepts PostgreSQL SSLRequest before TLS 1.3. It may decline
one GSS encryption request with `N` before SSLRequest. Plaintext startup,
plaintext cancellation, repeated GSS requests and direct TLS are closed.
Configure stock clients with `sslmode=verify-full`, the issuing CA,
`gssencmode=disable`, and `channel_binding=require` when supported. The server offers
SCRAM-SHA-256-PLUS using the signature-hash digest of the exact leaf DER served
by TLS. Authenticated TLS clients that disable channel binding can still use
ordinary SCRAM-SHA-256. Clients must verify the configured hostname.

TLS memory is reserved for the configured connection slots at startup and
charged to the server region ledger. `--capabilities` shows the configured TLS
policy and TLS bytes per slot. Under overload the server closes new sockets
without sending plaintext. Encrypted cancellation requires a free slot.
The initial TLS policy supports a single PostgreSQL listener. Additional
protocol listeners, cluster mode, and parallel reactors require separate
qualification.

With both TLS options omitted, the existing PostgreSQL negotiation and plaintext
policy apply. The release stage and production-readiness flags stay unchanged.
