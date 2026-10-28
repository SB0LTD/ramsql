import pathlib,subprocess,zipfile
folder=pathlib.Path('.sig-cache/routing-diagnostic-src').resolve()
archive=pathlib.Path('.sig-cache/routing-diagnostic-src.zip').resolve()
subprocess.run(['git','archive','--format=zip','-o',str(archive),'HEAD'],check=True)
folder.mkdir(exist_ok=False)
with zipfile.ZipFile(archive) as z:z.extractall(folder)
path=folder/'src/server.sig';s=path.read_text()
def replace(old,new):
 global s
 assert s.count(old)==1,(old,s.count(old))
 s=s.replace(old,new)
replace('    fn dispatchRead(self:*Client) !bool {','''    fn routeDiagnostic(tag:u8,reason:[]const u8) void {
        var buffer:[160]u8=undefined;
        const line=std.fmt.bufPrint(&buffer,"ROUTE {c} {s}\\n",.{tag,reason}) catch return;
        sig.io.File.stderr().writeStreamingAll(.{},line) catch {};
    }
    fn dispatchRead(self:*Client) !bool {
        const trace_input=self.input[self.input_start..][0..self.input_len];
        const trace=trace_input.len!=0 and (trace_input[0]=='E' or trace_input[0]=='D');
        const trace_tag:u8=if(trace) trace_input[0] else 0;''')
replace('if(!parallel or self.session!=.postgres or !self.connection.detachedReadEligible()) return false;','if(!parallel or self.session!=.postgres or !self.connection.detachedReadEligible()) {if(trace) routeDiagnostic(trace_tag,"connection ineligible");return false;}')
replace('const candidate=session.detachedCandidate(input) orelse return false;','const candidate=session.detachedCandidate(input) orelse {if(trace) routeDiagnostic(trace_tag,"frame ineligible");return false;};')
replace('if(!Raw.queryCandidate(text)) return false;','if(!Raw.queryCandidate(text)) {if(trace) routeDiagnostic(trace_tag,"not a query candidate");return false;}')
replace('if(self.output.len-self.output_len<required) return false;','if(self.output.len-self.output_len<required) {if(trace) routeDiagnostic(trace_tag,"output capacity");return false;}')
replace('const reader=self.reader orelse return false;','const reader=self.reader orelse {if(trace) routeDiagnostic(trace_tag,"no reader");return false;};')
replace('if(reader.context==null and monotonicClock()<reader.retry_at) return false;','if(reader.context==null and monotonicClock()<reader.retry_at) {if(trace) routeDiagnostic(trace_tag,"reader retry delay");return false;}')
replace('reader.provision() catch {reader.retry_at=monotonicClock()+1000000000;return false;};','reader.provision() catch |err| {if(trace) routeDiagnostic(trace_tag,@errorName(err));reader.retry_at=monotonicClock()+1000000000;return false;};')
replace('            else=>return false,','            else=>{if(trace) routeDiagnostic(trace_tag,@errorName(err));return false;},')
replace('if(source!=null and prepared and context.needsZoneResolver()) return false;','if(source!=null and prepared and context.needsZoneResolver()) {if(trace) routeDiagnostic(trace_tag,"named zone resolver");return false;}')
replace('        self.connection.read_context=context;self.connection.running_statement=true;','        if(trace) routeDiagnostic(trace_tag,"shared context selected");\n        self.connection.read_context=context;self.connection.running_statement=true;')
path.write_text(s)
print(folder)
