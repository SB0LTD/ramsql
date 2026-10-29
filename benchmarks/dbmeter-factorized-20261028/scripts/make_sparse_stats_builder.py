from pathlib import Path
p=Path('.sig-cache/build_sparse.py')
s=p.read_text().replace("name='sparse-'+mode", "name='sparse-stats-'+mode")
diagnostic=r'''
        const measured = context.workspace.query.stream_stats;
        if (candidate == .execute and (measured.factor_plans != 0 or measured.nested_factor_plans != 0)) {
            var stats_buffer: [1024]u8 = undefined;
            const stats_line = try sig.fmt.bufPrint(&stats_buffer,
                "DIAGNOSTIC factor={d} keys={d} selected={d} nested={d} nested_keys={d} rejected_blocks={d} sparse_masks={d} sparse_rows={d} rows={d} groups={d} probes={d}\n",
                .{measured.factor_plans, measured.factor_keys, measured.factor_selected_rows,
                  measured.nested_factor_plans, measured.nested_factor_keys,
                  measured.presence_rejected_blocks, measured.sparse_factor_masks,
                  measured.sparse_factor_rows, measured.rows, measured.groups, measured.probes});
            try sig.io.File.stderr().writeStreamingAll(.{},stats_line);
        }
'''
anchor='record=dict(ramsql_source=base'
extra="p=source/'src/server.sig';s=p.read_text();anchor='        const reply=try session.handle(&self.connection,input,output);';assert s.count(anchor)==1;p.write_text(s.replace(anchor,anchor+"+repr(diagnostic)+"))\n"
assert s.count(anchor)==1
s=s.replace(anchor,extra+anchor)
Path('.sig-cache/build_sparse_stats.py').write_text(s)
