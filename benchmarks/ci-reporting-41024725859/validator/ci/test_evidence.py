"""Synthetic gate unit fixtures are never benchmark evidence or live results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import evidence
from bootstrap import VERSIONS
from live_benchmark import PROFILES, ENGINES, cases, fresh_seed
from runner import actions_context, validate_same_run


class GateContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.campaign = dict(schema=1, suite='sales-subset', profile='qualification', policy=PROFILES['qualification'],
                             expected_cases=[f'{name}/{clients}' for name, clients in cases('sales-subset')],
                             checks=dict.fromkeys(ENGINES, True), completed=True, gaps=[], runs=[],
                             original_input_sha256={'unit-test-fixture': 'a' * 64},
                             input_revision='b' * 40,
                             ci=dict(provider='github-actions', repository='benor1470/dbMeter', run_id='123',
                                     run_attempt='1', runner_name='synthetic-unit-fixture', runner_os='Linux', checkout_revision='b' * 40),
                             ramsql=dict(source=copy.deepcopy(VERSIONS['ramsql']), binary_sha256='c' * 64))
        for engine in ENGINES:
            xml = ET.Element('testsuite')
            for name in {'a_totals'} | evidence.MIXED['sales-subset']:
                ET.SubElement(xml, 'testcase', name=name)
            ET.ElementTree(xml).write(self.folder / (engine + '-checks.xml'))
            for scenario, clients in cases('sales-subset'):
                for trial in (1, 2, 3):
                    count = 1000 if engine == 'ramsql' else 200
                    latency = 16 if engine == 'ramsql' else 24
                    raw = dict(dbmeterReport=1, measuredStart=100000, measuredEnd=160000,
                               database=dict(label=engine),
                               nodes=[dict(settings=dict(threads=clients, duration='PT1M', warmup='PT15S', rampUp='PT5S'))],
                               scenarios={name: dict(count=count, errors=0, latencyMicros=dict(subBits=5, count=count, min=latency, max=latency, buckets={str(latency): count})) for name in evidence.MIXED['sales-subset']})
                    name = f'{engine}-{clients}-{trial}.json'
                    (self.folder / name).write_text(json.dumps(raw))
                    self.campaign['runs'].append(dict(engine=engine, case=f'{scenario}/{clients}', trial=trial, returncode=0, report=name))

    def result(self):
        (self.folder / 'campaign.json').write_text(json.dumps(self.campaign))
        return evidence.summarize(self.folder)

    def mutate_raw(self, change, engine='ramsql'):
        for run in self.campaign['runs']:
            if run['engine'] == engine:
                path = self.folder / run['report']
                raw = json.loads(path.read_text())
                change(raw)
                path.write_text(json.dumps(raw))

    def test_complete_qualification_fixture_passes(self):
        self.assertTrue(self.result()['qualified'])

    def test_smoke_can_never_qualify(self):
        self.campaign.update(profile='smoke', policy=PROFILES['smoke'])
        self.assertFalse(self.result()['qualified'])

    def test_local_campaign_cannot_qualify(self):
        self.campaign.pop('ci')
        self.assertFalse(self.result()['qualified'])

    def test_diagnostic_campaign_cannot_qualify(self):
        self.campaign['diagnostic_only'] = True
        self.assertFalse(self.result()['qualified'])

    def test_wrong_ci_provider_cannot_qualify(self):
        self.campaign['ci']['provider'] = 'local'
        self.assertFalse(self.result()['qualified'])

    def test_missing_runner_identity_cannot_qualify(self):
        self.campaign['ci'].pop('runner_name')
        self.assertFalse(self.result()['qualified'])

    def test_missing_ci_attempt_cannot_qualify(self):
        self.campaign['ci'].pop('run_attempt')
        self.assertFalse(self.result()['qualified'])

    def test_different_ci_checkout_cannot_qualify(self):
        self.campaign['ci']['checkout_revision'] = 'c' * 40
        self.assertFalse(self.result()['qualified'])

    def test_mixed_ci_run_attempts_rejected(self):
        current = copy.deepcopy(self.campaign['ci'])
        validate_same_run(self.campaign['ci'], current)
        for key, value in (('run_id', '456'), ('run_attempt', '2'), ('checkout_revision', 'c' * 40)):
            changed = dict(current, **{key: value})
            with self.assertRaises(ValueError):
                validate_same_run(changed, current)

    def test_local_throughput_entry_point_rejected(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            actions_context('b' * 40)

    def test_error_cannot_be_counted_as_fast_query(self):
        self.mutate_raw(lambda raw: next(iter(raw['scenarios'].values())).update(errors=1))
        self.assertFalse(self.result()['qualified'])

    def test_empty_scenario_blocks_claim(self):
        self.mutate_raw(lambda raw: next(iter(raw['scenarios'].values())).update(count=0))
        self.assertFalse(self.result()['qualified'])

    def test_missing_reference_blocks_claim(self):
        self.campaign['runs'] = [r for r in self.campaign['runs'] if r['engine'] != 'irdb']
        self.assertFalse(self.result()['qualified'])

    def test_missing_trial_blocks_claim(self):
        self.campaign['runs'].pop()
        self.assertFalse(self.result()['qualified'])

    def test_duplicate_trial_blocks_claim(self):
        self.campaign['runs'].append(copy.deepcopy(self.campaign['runs'][0]))
        self.assertFalse(self.result()['qualified'])

    def test_wrong_client_count_blocks_claim(self):
        self.mutate_raw(lambda raw: raw['nodes'][0]['settings'].update(threads=99))
        self.assertFalse(self.result()['qualified'])

    def test_wrong_engine_label_blocks_claim(self):
        self.mutate_raw(lambda raw: raw['database'].update(label='postgres'))
        self.assertFalse(self.result()['qualified'])

    def test_overlapping_trial_ranges_block_claim(self):
        run = self.campaign['runs'][0]
        path = self.folder / run['report']
        raw = json.loads(path.read_text())
        raw['measuredEnd'] = 700000
        path.write_text(json.dumps(raw))
        result = self.result()
        self.assertTrue(any(row.get('ratio_to_fastest', 0) >= 2 for row in result['rows']))
        self.assertFalse(result['qualified'])

    def test_short_window_blocks_claim(self):
        self.mutate_raw(lambda raw: raw.update(measuredEnd=100001))
        self.assertFalse(self.result()['qualified'])

    def test_short_requested_phase_blocks_claim(self):
        self.mutate_raw(lambda raw: raw['nodes'][0]['settings'].update(duration='PT3S'))
        self.assertFalse(self.result()['qualified'])

    def test_dropped_mixed_query_blocks_claim(self):
        self.mutate_raw(lambda raw: raw['scenarios'].pop('s6_store_category_mix'))
        self.assertFalse(self.result()['qualified'])

    def test_corrupt_histogram_blocks_claim(self):
        self.mutate_raw(lambda raw: next(iter(raw['scenarios'].values()))['latencyMicros'].update(count=5))
        self.assertFalse(self.result()['qualified'])

    def test_failed_correctness_blocks_claim(self):
        self.campaign['checks']['ramsql'] = False
        self.assertFalse(self.result()['qualified'])

    def test_missing_original_csv_check_blocks_claim(self):
        tree = ET.parse(self.folder / 'ramsql-checks.xml')
        tree.getroot().remove(list(tree.getroot())[0])
        tree.write(self.folder / 'ramsql-checks.xml')
        self.assertFalse(self.result()['qualified'])

    def test_skipped_correctness_blocks_claim(self):
        tree = ET.parse(self.folder / 'ramsql-checks.xml')
        ET.SubElement(list(tree.getroot())[0], 'skipped')
        tree.write(self.folder / 'ramsql-checks.xml')
        self.assertFalse(self.result()['qualified'])

    def test_insignificant_throughput_blocks_claim(self):
        self.mutate_raw(lambda raw: raw.update(measuredEnd=340000))
        self.assertFalse(self.result()['qualified'])

    def test_tail_latency_regression_blocks_claim(self):
        def change(raw):
            for value in raw['scenarios'].values():
                count = value['count']
                value['latencyMicros'].update(min=31, max=31, buckets={'31': count})
        self.mutate_raw(change)
        self.assertFalse(self.result()['qualified'])

    def test_source_substitution_blocks_claim(self):
        self.campaign['ramsql']['source']['revision'] = 'd' * 40
        self.assertFalse(self.result()['qualified'])

    def test_traversal_blocks_claim(self):
        self.campaign['runs'][0]['report'] = '../outside.json'
        self.assertFalse(self.result()['qualified'])

    def test_bucket_boundary_matches_documented_format(self):
        histogram = dict(subBits=5, count=2, min=32, max=33, buckets={'32': 1, '33': 1})
        self.assertEqual(evidence.percentile([histogram], 50), 32)
        self.assertEqual(evidence.percentile([histogram], 99), 33)

    def test_fresh_seed_omits_only_missing_table_prelude(self):
        original = self.folder / 'input.sql'
        target = self.folder / 'output.sql'
        body = b"CREATE TABLE sales(id INT);\nINSERT INTO sales VALUES(1);\n"
        original.write_bytes(b'-- original generator\nDROP TABLE sales;\n' + body)
        self.assertEqual(fresh_seed(original, target), ['sales'])
        self.assertEqual(target.read_bytes(), b'-- original generator\n' + body)
        self.assertIn(b'DROP TABLE sales;', original.read_bytes())

    def test_fresh_seed_rejects_drop_after_data(self):
        original = self.folder / 'input.sql'
        original.write_bytes(b'CREATE TABLE sales(id INT);\nDROP TABLE sales;\n')
        with self.assertRaises(ValueError):
            fresh_seed(original, self.folder / 'output.sql')

    def test_fresh_seed_rejects_unrelated_drop(self):
        original = self.folder / 'input.sql'
        original.write_bytes(b'DROP TABLE application_data;\n')
        with self.assertRaises(ValueError):
            fresh_seed(original, self.folder / 'output.sql')


if __name__ == '__main__':
    unittest.main()
