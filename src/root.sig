//! RAMSQL: allocator-free in-memory SQL, backed by ZPM Layer 0.
const sql = @import("sql");

pub const version = "0.3.0-alpha.1";
pub const Config = sql.Config;
pub const Error = sql.Error;
pub const Database = sql.Database;
pub const DefaultDatabase = Database(.{
    .tables = 4,
    .columns = 8,
    .rows_per_table = 128,
    .name_bytes = 32,
    .text_bytes = 128,
    .integer_bits = 32,
    .nulls_first_ascending = false,
});
