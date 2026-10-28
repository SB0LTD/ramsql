from pathlib import Path
p=Path('.sig-cache/routing-diagnostic-src/src/server.sig');s=p.read_text()
s=s.replace("const trace=trace_input.len!=0 and (trace_input[0]=='E' or trace_input[0]=='D');", "const trace=trace_input.len!=0 and trace_input[0]!='Q';")
old='reader.provision() catch |err| {if(trace) routeDiagnostic(trace_tag,@errorName(err));reader.retry_at=monotonicClock()+1000000000;return false;};'
assert s.count(old)==1
new='''reader.provision() catch |err| {
            if(trace) routeDiagnostic(trace_tag,@errorName(err));
            lockMemory();const stats=memory_ledger.stats();unlockMemory();
            var details:[180]u8=undefined;
            const message=std.fmt.bufPrint(&details,"used={d} free={d} metadata={d} scratch={d}",.{stats.used,stats.free,@sizeOf(Raw.ReadContext),Raw.ReadContext.backingBytesFor(selected_work_rows,selected_read_refs) catch 0}) catch "budget diagnostic failed";
            if(trace) routeDiagnostic(trace_tag,message);
            reader.retry_at=monotonicClock()+1000000000;return false;
        };'''
s=s.replace(old,new);p.write_text(s)
p=Path('.sig-cache/dbmeter_route_trace.py');s=p.read_text().replace('dbmeter-route-trace-workers8','dbmeter-route-trace-allframes-workers8');Path('.sig-cache/dbmeter_route_trace_all.py').write_text(s)
