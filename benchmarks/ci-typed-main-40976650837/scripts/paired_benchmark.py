"""Same-VM RAMSQL control/candidate diagnostic; never all-engine qualification."""
import json
import statistics
import sys

from bootstrap import ROOT, RUNTIME, VERSIONS, digest
from evidence import MIXED, check_xml, publish, read_run
from live_benchmark import Campaign
from runner import host_snapshot

ORDER = (('baseline', 'candidate'), ('candidate', 'baseline'), ('baseline', 'candidate'))
CASES = ('mixed/1', 'mixed/8', 's3_top_products/1', 's3_top_products/8')


def validate_sources(proofs):
    for mode, key in (('baseline', 'ramsql_baseline'), ('candidate', 'ramsql')):
        if proofs[mode]['source'] != VERSIONS[key]:
            raise ValueError('Unpinned paired source: ' + mode)
    for key in ('dependency_manifest_sha256', 'toolchain', 'build_arguments'):
        if not proofs['baseline'].get(key) or proofs['baseline'][key] != proofs['candidate'].get(key):
            raise ValueError('Paired library/toolchain/build arguments differ: ' + key)


def summarize_pairs(folder, campaign):
    validate_sources(campaign['paired_sources'])
    if campaign.get('diagnostic_only') is not True or campaign.get('merge_allowed') is not False:
        raise ValueError('Paired measurements must remain diagnostic')
    if campaign['expected_cases'] != list(CASES):
        raise ValueError('Paired matrix changed')
    if tuple(tuple(order) for order in campaign.get('paired_order', [])) != ORDER:
        raise ValueError('Paired order changed')
    scheduled = [(pair, mode, case) for pair, order in enumerate(ORDER, 1) for mode in order for case in CASES]
    if [(run['trial'], run['variant'], run['case']) for run in campaign['runs']] != scheduled:
        raise ValueError('Paired trial schedule is incomplete or reordered')
    expected = {(pair, mode, case) for pair in (1, 2, 3) for mode in ('baseline', 'candidate') for case in CASES}
    values = {}
    for run in campaign['runs']:
        key = (run['trial'], run['variant'], run['case'])
        if key not in expected or key in values:
            raise ValueError('Unexpected/duplicate paired trial')
        if run['binary_sha256'] != campaign['paired_sources'][run['variant']]['binary_sha256']:
            raise ValueError('Paired binary substitution')
        values[key] = read_run(folder, campaign, run)
    if values.keys() != expected:
        raise ValueError('Missing paired trial; partial runs are not scored')
    required = {'a_totals'} | MIXED['sales-subset']
    for pair in (1, 2, 3):
        for mode in ('baseline', 'candidate'):
            check_xml(folder / f'{mode}-pair{pair}-checks.xml', required)
    rows = []
    for case in CASES:
        row = dict(case=case, engines={}, paired_qps_ratios=[])
        for mode in ('baseline', 'candidate'):
            trials = [values[pair, mode, case] for pair in (1, 2, 3)]
            row['engines'][mode] = dict(median_qps=statistics.median(t['qps'] for t in trials),
                min_qps=min(t['qps'] for t in trials), max_qps=max(t['qps'] for t in trials),
                median_p99_us=statistics.median(t['p99_us'] for t in trials), trials=trials)
        row['paired_qps_ratios'] = [values[pair, 'candidate', case]['qps'] / values[pair, 'baseline', case]['qps'] for pair in (1, 2, 3)]
        rows.append(row)
    return dict(diagnostic_only=True, merge_allowed=False, rows=rows)


class PairedCampaign(Campaign):
    def __init__(self):
        super().__init__('sales-subset', 'qualification')
        self.candidate_binary = self.binary
        self.baseline_binary = RUNTIME / 'downloaded-baseline/ramsql/bin/ramsql'
        self.baseline_binary.chmod(0o755)
        baseline = json.loads((RUNTIME / 'downloaded-baseline/ramsql-provenance.json').read_text())
        if digest(self.baseline_binary) != baseline['binary_sha256']:
            raise ValueError('Downloaded baseline artifact changed')
        self.report.update(diagnostic_only=True, merge_allowed=False,
            scope='Paired RAMSQL control/candidate; not reference-engine or full-suite qualification',
            expected_cases=list(CASES), paired_order=ORDER,
            paired_sources=dict(baseline=baseline, candidate=self.ram_provenance), paired_admission={})
        validate_sources(self.report['paired_sources'])
        self.save()

    def run(self):
        if self.command('original-generator', [sys.executable, '-I', str(self.folder / 'gen.py')]):
            raise RuntimeError('Original generator failed')
        self.setup()
        if not all(self.report['checks'].get(engine) for engine in ('ramsql', 'postgres', 'irdb')):
            self.report['gaps'].append('Paired mixed timing requires all original checks to pass first')
            self.save()
            return
        for pair, order in enumerate(ORDER, 1):
            for mode in order:
                self.binary = self.candidate_binary if mode == 'candidate' else self.baseline_binary
                with self.running('ramsql'):
                    self.report['paired_admission'][f'{mode}/{pair}'] = self.report['ramsql_machine_capacity']
                    for source in self.data_files:
                        self.psql(source)
                    name = f'{mode}-pair{pair}-checks'
                    checks = self.out / (name + '.xml')
                    if self.command(name, self.java('check', 'ramsql', ['--junit', str(checks)])):
                        raise RuntimeError('Original full-suite checks failed: ' + mode)
                    check_xml(checks, {'a_totals'} | MIXED['sales-subset'])
                    for case in CASES:
                        scenario, clients = case.split('/')
                        name = f'{mode}-pair{pair}-{scenario}-{clients}'
                        target = self.out / name
                        tail = ['--set', 'load.threads=' + clients, '--set', 'load.warmup=15s',
                                '--set', 'load.duration=60s', '--set', 'load.rampUp=5s',
                                '--node', 'local', '--run-id', name, '--out', str(target)]
                        if scenario != 'mixed':
                            tail += ['--only', scenario]
                        before = host_snapshot()
                        code = self.command(name, self.java('load', 'ramsql', tail))
                        raw = target / 'local.json'
                        self.report['runs'].append(dict(engine='ramsql', variant=mode, trial=pair, case=case,
                            binary_sha256=self.report['paired_sources'][mode]['binary_sha256'], returncode=code,
                            report=str(raw.relative_to(self.out)) if raw.exists() else None,
                            host_before=before, host_after=host_snapshot()))
                        self.save()
                        read_run(self.out, self.report, self.report['runs'][-1])
        self.report['completed'] = True
        self.save()

    def publish(self):
        try:
            result = summarize_pairs(self.out, self.report)
        except (ValueError, KeyError, OSError, TypeError) as error:
            result = dict(diagnostic_only=True, merge_allowed=False, rows=[], error=str(error))
        (self.out / 'paired-summary.json').write_text(json.dumps(result, indent=2) + '\n')
        lines = ['# Same-runner RAMSQL A/B diagnostic', '',
                 '**Not eligible for the all-engine advantage gate or a general performance claim.**', '',
                 self.report['ci']['run_url'], '',
                 '| Original case / clients | Baseline QPS median | Candidate QPS median | Paired candidate/baseline ratios |',
                 '| --- | ---: | ---: | --- |']
        for row in result['rows']:
            lines.append('| ' + ' | '.join([row['case'], f"{row['engines']['baseline']['median_qps']:.2f}",
                f"{row['engines']['candidate']['median_qps']:.2f}", ', '.join(f'{r:.3f}' for r in row['paired_qps_ratios'])]) + ' |')
        lines += ['', 'AB / BA / AB, same runner and library/toolchain, fresh RAMSQL load and original full checks per variant/pair, fresh JVM per case, original 5s ramp / 15s warmup / 60s measure. Every trial, p99, source/binary checksum and host pressure snapshot is retained. No post-hoc subset is substituted.', '']
        lines += ['- ' + gap for gap in self.report['gaps']]
        for error in (self.report.get('error'), result.get('error')):
            if error:
                lines += ['- ' + error]
        text = '\n'.join(lines) + '\n'
        (self.out / 'paired-summary.md').write_text(text)
        publish(text)


def main():
    campaign = PairedCampaign()
    try:
        campaign.run()
    except BaseException as error:
        campaign.report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        campaign.save()
        campaign.publish()


if __name__ == '__main__':
    main()
