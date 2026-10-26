"""Oracle/trace integrity tests; independent live PostgreSQL replay is separate."""
import tempfile
from pathlib import Path
import struct
import unittest
from production_workloads import CASES, MAGIC, digest, requests, row, write_fixture
from benchmark_production import validate


class Contracts(unittest.TestCase):
    def test_typed_digest_distinguishes_null_empty_and_signed_values(self):
        values=[digest([(v,)],1) for v in (None,'',0,1,-1,'0')]
        self.assertEqual(len(set(values)),6)
        self.assertNotEqual(digest([(1,2)],2),digest([(2,1)],2))
        self.assertNotEqual(digest([],1),digest([],2))

    def test_every_write_changes_state_and_every_cycle_restores_it(self):
        for case in CASES:
            data=[row(i,17,96,42) for i in range(128)]
            trace=requests(case,data,42,160)
            self.assertTrue(trace)
            self.assertTrue(all(q[0] in (1,2) for q in trace))
            if case=='mixed_95_5':
                self.assertEqual(sum(q[0]==2 for q in trace)*20,len(trace))
            if case=='mixed_50_50':
                self.assertEqual(sum(q[0]==2 for q in trace)*2,len(trace))
            state={r[0]:r for r in data}
            for _ in range(2):
                for kind,affected,_,_,sql in trace:
                    if kind!=2:continue
                    if sql.startswith('UPDATE'):
                        import re
                        match=re.fullmatch(r'UPDATE bench SET value=(\d+) WHERE id=(\d+)',sql)
                        value,key=map(int,match.groups());old=state[key]
                        self.assertNotEqual(old[1],value)
                        state[key]=(key,value,old[2],old[3])
                    elif sql.startswith('DELETE'):
                        state.pop(int(sql.split('=')[1]))
                    else:
                        key=int(sql.split('(')[1].split(',')[0]);self.assertNotIn(key,state);state[key]=data[key]
                    self.assertEqual(affected,1)
                self.assertEqual(state,{r[0]:r for r in data})

    def test_binary_fixture_is_deterministic_and_records_all_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            files=[Path(folder)/x for x in ('a','b')]
            for f in files:write_fixture(f,n=128,groups=17,width=512,seed=42,case='uniform_get',count=32)
            self.assertEqual(files[0].read_bytes(),files[1].read_bytes())
            raw=files[0].read_bytes();self.assertEqual(raw[:8],MAGIC)
            self.assertEqual(struct.unpack_from('<IIIIIQ',raw,8),(128,17,512,32,1,42))

    def test_results_cannot_hide_failure_short_timing_or_missing_operations(self):
        fixture=dict(rows=128,requests=32)
        rows=[dict(metadata=True,engine='ramsql-native',rows=128,requests=32,warmup_ns=1000000000,warmup_cycles=1)]+[
            dict(trial=i,passed=True,cycles=2,operations=64,elapsed_ns=200000000,p50_upper_ns=8,p95_upper_ns=16,p99_upper_ns=32) for i in range(5)]
        validate(rows,fixture,200)
        for field,value in [('passed',False),('operations',63),('cycles',0),('elapsed_ns',1),('p99_upper_ns',4)]:
            bad=[dict(r) for r in rows];bad[1][field]=value
            with self.assertRaises(AssertionError):validate(bad,fixture,200)
        with self.assertRaises(AssertionError):validate(rows[:-1],fixture,200)


if __name__=='__main__':unittest.main(verbosity=2)
