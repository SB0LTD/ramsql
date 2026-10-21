//! Executable consumer contract through the public RAMSQL module.
const ram = @import("ramsql");

pub fn main() !void {
    const DB = ram.Database(.{ .tables = 1, .columns = 2, .rows_per_table = 2, .name_bytes = 16, .text_bytes = 16 });
    var db: DB = .{};
    var result: DB.Result = .{};
    try db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)", &result);
    try db.execute("INSERT INTO items VALUES (1, 'ramsql')", &result);
    if (result.affected_rows != 1) return error.InsertContractFailed;
    try db.execute("SELECT name FROM items WHERE id = 1", &result);
    if (result.row_count != 1 or result.column_count != 1) return error.QueryContractFailed;
    const name = result.rows[0].cells[0].text.slice();
    if (name.len != 6) return error.TextContractFailed;
    for (name, "ramsql") |actual, expected| if (actual != expected) return error.TextContractFailed;
    try db.execute("DELETE FROM items WHERE id = 1", &result);
    if (result.affected_rows != 1) return error.DeleteContractFailed;
    try db.execute("DROP TABLE items", &result);
    if (@sizeOf(ram.DefaultDatabase) == 0) return error.EmptyDatabase;
}
