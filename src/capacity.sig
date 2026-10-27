//! Machine observation/admission policy. OS mechanisms and byte accounting
//! remain in ZPM; this module chooses RAMSQL's headroom and reports the plan.
const std=@import("std");
const sig=@import("sig");
const pages=@import("page_region");
const resources=@import("machine_resources");
const budget=@import("resource_budget");
pub const Sample=struct {observation:resources.Observation,plan:budget.Plan,new_region_bytes:u64};
pub fn sample(hard:?u64,headroom:?u64,owned:u64) !Sample {
    var amount:usize=6*4096;var migrations:usize=0;
    while(true) {
        var scratch=try pages.reserve(amount);defer scratch.close();
        const buffers=try resources.Buffers.partition(scratch.bytes);
        const observation=resources.observe(buffers,owned) catch |err| switch(err) {
            error.BufferTooSmall=>{const next=@mulWithOverflow(amount,2);if(next[1]!=0) return error.ArithmeticOverflow;amount=next[0];continue;},
            error.ChangedDuringProbe=>{migrations+=1;if(migrations>=3) return err;continue;},
            else=>return err,
        };
        // Existing static WAL/recovery buffers are already process ownership;
        // runtime page regions are charged explicitly by RAMSQL's ledger.
        const plan=try budget.plan(observation.machine,.{.hard_process_bytes=hard,.headroom_bytes=headroom,.recovery_bytes=0});
        return .{.observation=observation,.plan=plan,
            .new_region_bytes=plan.process_bytes -| @max(observation.machine.process_committed,observation.machine.process_resident)};
    }
}
pub fn describe(hard:?u64,headroom:?u64,legacy_bytes:u64) !void {
    const detected=try sample(hard,headroom,0);
    const observation=detected.observation;const machine=observation.machine;const plan=detected.plan;
    var buffer:[4096]u8=undefined;
    const text=try std.fmt.bufPrint(&buffer,
        "{{\"schema\":1,\"scope\":\"machine_capacity\",\"usable_cpus\":{d},\"cpu_quota_milli\":{?d},\"physical_cores\":{?d},\"numa_localities\":{?d},\"physical_total\":{d},\"available_physical\":{d},\"available_kind\":\"{s}\",\"process_resident\":{d},\"process_committed\":{d},\"available_commit\":{?d},\"available_address_space\":{?d},\"scope_limit\":{?d},\"scope_used\":{?d},\"visible_scope_levels\":{d},\"immediate_job\":{s},\"commit_source\":\"{s}\",\"headroom_bytes\":{d},\"process_budget_bytes\":{d},\"shared_pool_bytes\":{d},\"preferred_storage_bytes\":{d},\"preferred_execution_bytes\":{d},\"suggested_cpu_workers\":{d},\"admitted_tasks\":{d},\"new_region_bytes\":{d},\"legacy_startup_bytes\":{d},\"pressure\":{s},\"server_region_budgeting\":true,\"elastic_sql_storage\":false,\"parallel_sql\":false}}\n",
        .{machine.usable_cpus,machine.cpu_quota_milli,observation.physical_cores,observation.numa_localities,machine.physical_total,machine.available_physical,
        @tagName(observation.available_kind),machine.process_resident,machine.process_committed,machine.available_commit,machine.available_address_space,
        machine.scope_limit,machine.scope_used,observation.visible_scope_levels,if(observation.immediate_job) "true" else "false",@tagName(observation.commit_source),
        plan.headroom_bytes,plan.process_bytes,plan.shared_pool_bytes,plan.preferred_storage_bytes,plan.preferred_execution_bytes,plan.cpu_workers,plan.admitted_tasks,
        detected.new_region_bytes,legacy_bytes,if(plan.pressure) "true" else "false"});
    try sig.io.File.stdout().writeStreamingAll(.{},text);
}
