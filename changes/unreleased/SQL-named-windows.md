Named WINDOW definitions and scoped inheritance are available in the shared
allocator-free SQL layer. Corrects volatile ordered aggregate evaluation and
shared named sort keys, with explicit rejection of unsafe demand combinations.
Adds native PostgreSQL differential, binary/parameter and durable replay checks.
This does not establish complete window or SQL compatibility.
