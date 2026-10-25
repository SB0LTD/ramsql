"""Integrity checks for comparative benchmark results; no synthetic speed claims."""
import unittest
from benchmark_review import digest,expected,validate_samples,MASK

class Contracts(unittest.TestCase):
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

if __name__=='__main__': unittest.main(verbosity=2)
