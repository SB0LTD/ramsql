"""Integrity checks for comparative benchmark results; no synthetic speed claims."""
import unittest
from profile_reports import compact_report
from benchmark_review import digest,expected,validate_samples,validate_stream_samples,MASK

class Contracts(unittest.TestCase):
    def test_ordered_probe_evidence(self):
        samples=[dict(stream_rows=0,stream_groups=0,stream_probes=0,group_cells=0,group_refs=0,
                      key_walks=1,key_successes=1,key_fallbacks=0,key_probes=968,nonnull_sort_keys=0) for _ in range(5)]
        validate_stream_samples('filtered_limit',samples,1000000)
        for field,value in [('key_probes',967),('key_successes',0),('nonnull_sort_keys',2)]:
            bad=[dict(s) for s in samples];bad[0][field]=value
            with self.assertRaises(AssertionError):validate_stream_samples('filtered_limit',bad,1000000)

    def test_profile_compaction_keeps_every_cpu_row(self):
        report='10.00% [.] outer(.{ .inner=.{ .text="{quoted}" }, .n=3 }).scan\n20.00% [.] '+('long_symbol'*100)+'\n'
        compact=compact_report(report)
        self.assertIn('10.00% [.] outer(config).scan',compact)
        self.assertIn('20.00% [.]',compact)
        self.assertEqual(compact.count('%'),2)

    def test_models_against_materialized_rows(self):
        for n in (64,128,512,8192):
            for changed in (False,True):
                with self.subTest(n=n,changed=changed):
                    data=[(i,7 if changed and i==n//2 else n-i,i%64) for i in range(n)]
                    oracle={
                        "primary_key_hot":[data[n//2][:2]],
                        "filtered_limit":[r[:2] for r in data if r[2]==7][:16],
                        "sort_limit":[r[:2] for r in sorted(data,key=lambda r:(r[1],r[0]))[:16]],
                    }
                    got=expected(n,changed)
                    for name,rows in oracle.items():self.assertEqual(got[name],digest(rows))
                    if not changed:
                        self.assertEqual(got['aggregate'],digest([(n,sum(r[1] for r in data))]))
                        groups=[(g,sum(r[2]==g for r in data),sum(r[1] for r in data if r[2]==g)) for g in range(64)]
                        self.assertEqual(got['group_aggregate'],digest(groups))

    def test_timing_cannot_hide_wrong_results_or_trials(self):
        wanted=expected(512)['sort_limit'];iterations=7
        samples=[dict(trial=i,operations=iterations,elapsed_ns=100,checksum=(wanted*iterations)&MASK) for i in range(5)]
        validate_samples('sort_limit',samples,512,iterations)
        for field,value in [('checksum',0),('operations',iterations+1),('elapsed_ns',0),('trial',4)]:
            with self.subTest(field=field):
                bad=[dict(s) for s in samples];bad[0][field]=value
                with self.assertRaises(AssertionError):validate_samples('sort_limit',bad,512,iterations)
        with self.assertRaises(AssertionError):validate_samples('sort_limit',samples[:4],512,iterations)

    def test_stream_evidence_cannot_hide_missing_input_or_stale_work(self):
        samples=[dict(stream_rows=1000000,stream_groups=64,stream_probes=1000000,group_cells=512,group_refs=1460) for _ in range(5)]
        validate_stream_samples('group_aggregate',samples,1000000)
        for field,value in [('stream_rows',512),('stream_groups',63),('stream_probes',0),('group_cells',-1),('group_refs',None)]:
            with self.subTest(field=field):
                bad=[dict(s) for s in samples];bad[0][field]=value
                with self.assertRaises(AssertionError):validate_stream_samples('group_aggregate',bad,1000000)
        with self.assertRaises(AssertionError):validate_stream_samples('primary_key_hot',samples,1000000)
        validate_stream_samples('aggregate',[dict(stream_rows=1000000,stream_groups=1,stream_probes=0,group_cells=2,group_refs=0) for _ in range(5)],1000000)
        validate_stream_samples('group_aggregate',[{} for _ in range(5)],1000000) # preserved old binary
        summary=[dict(stream_rows=0,stream_groups=1,stream_probes=0,group_cells=2,group_refs=0,summary_rows=1000000,summary_plans=1) for _ in range(5)]
        validate_stream_samples('aggregate',summary,1000000)
        grouped=[dict(s,stream_groups=64,group_refs=128,group_cells=192) for s in summary]
        validate_stream_samples('group_aggregate',grouped,1000000)
        bad=[dict(s) for s in grouped];bad[0]['stream_probes']=1
        with self.assertRaises(AssertionError):validate_stream_samples('group_aggregate',bad,1000000)
        for field,value in [('summary_rows',512),('summary_plans',2),('stream_rows',1000000)]:
            bad=[dict(s) for s in summary];bad[0][field]=value
            with self.assertRaises(AssertionError):validate_stream_samples('aggregate',bad,1000000)

if __name__=='__main__': unittest.main(verbosity=2)
