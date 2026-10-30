"""Synthetic A/B evidence fixtures only; these are never benchmark results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from bootstrap import VERSIONS
from evidence import MIXED
from live_benchmark import PROFILES
from paired_benchmark import CASES, ORDER, summarize_pairs, paired_cases


class PairedContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        proofs = {mode: dict(source=copy.deepcopy(VERSIONS[key]), binary_sha256=char * 64,
            dependency_manifest_sha256='e' * 64, toolchain=VERSIONS['sig'], build_arguments=['build', '-Doptimize=ReleaseFast'])
            for mode, key, char in (('baseline', 'ramsql_baseline', 'a'), ('candidate', 'ramsql', 'b'))}
        self.campaign = dict(suite='sales-subset', profile='qualification', policy=PROFILES['qualification'],
            diagnostic_only=True, merge_allowed=False, expected_cases=list(CASES), paired_order=ORDER, paired_sources=proofs, runs=[])
        for pair, order in enumerate(ORDER, 1):
            for mode in order:
                xml = ET.Element('testsuite')
                for name in {'a_totals'} | MIXED['sales-subset']:
                    ET.SubElement(xml, 'testcase', name=name)
                ET.ElementTree(xml).write(self.folder / f'{mode}-pair{pair}-checks.xml')
                for case in CASES:
                    scenario, clients = case.split('/')
                    count = 200 if mode == 'candidate' else 100
                    names = MIXED['sales-subset'] if scenario == 'mixed' else {scenario}
                    raw = dict(dbmeterReport=1, measuredStart=100000, measuredEnd=160000,
                        database=dict(label='ramsql'), nodes=[dict(settings=dict(threads=int(clients), duration='PT1M', warmup='PT15S', rampUp='PT5S'))],
                        scenarios={name: dict(count=count, errors=0, latencyMicros=dict(subBits=5, count=count, min=10, max=10, buckets={'10': count})) for name in names})
                    path = f'{mode}-{pair}-{scenario}-{clients}.json'
                    (self.folder / path).write_text(json.dumps(raw))
                    self.campaign['runs'].append(dict(engine='ramsql', variant=mode, trial=pair, case=case,
                        binary_sha256=proofs[mode]['binary_sha256'], returncode=0, report=path))

    def test_complete_three_pairs(self):
        result = summarize_pairs(self.folder, self.campaign)
        self.assertFalse(result['merge_allowed'])
        self.assertTrue(result['diagnostic_only'])
        self.assertEqual(len(result['rows']), 4)
        for row in result['rows']:
            self.assertEqual(row['paired_qps_ratios'], [2, 2, 2])

    def test_focus_keeps_mix_and_original_scenario_only(self):
        for focus in MIXED['sales-subset']:
            self.assertEqual(paired_cases(focus), ('mixed/1', 'mixed/8', focus + '/1', focus + '/8'))
        for focus in ('mixed', 'new_faster_query', ''):
            with self.assertRaises(ValueError):
                paired_cases(focus)

    def test_missing_trial_rejected(self):
        self.campaign['runs'].pop()
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_duplicate_or_reordered_trial_rejected(self):
        self.campaign['runs'][1] = self.campaign['runs'][0]
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_sql_error_rejected(self):
        path = self.folder / self.campaign['runs'][0]['report']
        raw = json.loads(path.read_text())
        next(iter(raw['scenarios'].values()))['errors'] = 1
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_binary_substitution_rejected(self):
        self.campaign['runs'][0]['binary_sha256'] = 'c' * 64
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_different_library_rejected(self):
        self.campaign['paired_sources']['baseline']['dependency_manifest_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)

    def test_failed_fresh_check_rejected(self):
        path = self.folder / 'baseline-pair1-checks.xml'
        xml = ET.parse(path)
        ET.SubElement(list(xml.getroot())[0], 'failure')
        xml.write(path)
        with self.assertRaises(ValueError):
            summarize_pairs(self.folder, self.campaign)


if __name__ == '__main__':
    unittest.main()
