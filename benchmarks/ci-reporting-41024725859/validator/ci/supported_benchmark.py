"""CI optimization diagnostics; original failing checks remain merge blockers.

Only names of existing reviewer cases are selected. SQL, data, expected CSVs,
JDBC code, timing phases and the separate all-case advantage gate are unchanged.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys

from bootstrap import ROOT
from evidence import check_xml, read_run
from live_benchmark import Campaign, ENGINES
from runner import host_snapshot


class SupportedCampaign(Campaign):
    def __init__(self, suite):
        super().__init__(suite, 'qualification')
        prefix = 'q' if suite == 'sales-full' else 's'
        self.selected = (prefix + '5_high_value_customers', prefix + '6_store_category_mix')
        self.report.update(diagnostic_only=True, merge_allowed=False,
                           scope='Selected original read cases only; never all-case qualification',
                           expected_cases=[f'{case}/{clients}' for case in self.selected for clients in (1, 8)],
                           supported_checks={})
        self.save()

    def seed_ramsql(self, engine):
        if engine == 'ramsql':
            for source in self.data_files:
                self.psql(source)

    def supported_check(self, engine, name):
        target = self.out / (name + '.xml')
        code = self.command(name, self.java('check', engine, ['--only', ','.join(('a_totals', *self.selected)), '--junit', str(target)]))
        if code:
            raise RuntimeError(engine + ' failed selected original correctness checks')
        check_xml(target, {'a_totals', *self.selected})

    def run(self):
        generator = self.folder / ('gen_expected.py' if self.suite == 'sales-full' else 'gen.py')
        if self.command('original-generator', [sys.executable, '-I', str(generator)]):
            raise RuntimeError('Original generator failed')
        # Keep every original full-suite failure. The independent advantage job
        # only consumes live-* artifacts, never these diagnostic-* artifacts.
        self.setup()
        for engine in ENGINES:
            if self.report['checks'].get(engine) is False and engine == 'ramsql' and 'ramsql_machine_capacity' in self.report:
                log = self.out / 'ramsql-server.log'
                if log.exists() and any(text in log.read_text(errors='replace') for text in ('error: BudgetExceeded', 'error: MemoryReservationFailed')):
                    self.report['gaps'].append('Selected-read diagnostic cannot bypass native full-dataset capacity rejection')
                    self.save()
                    return
            with self.running(engine):
                self.seed_ramsql(engine)
                self.supported_check(engine, engine + '-selected-checks')
                self.report['supported_checks'][engine] = True
                self.save()
        for trial in range(3):
            order = ENGINES[trial % 3:] + ENGINES[:trial % 3]
            for engine in order:
                with self.running(engine):
                    self.seed_ramsql(engine)
                    self.supported_check(engine, f'{engine}-trial{trial + 1}-selected-checks')
                    for case in self.selected:
                        for clients in (1, 8):
                            name = f'{engine}-trial{trial + 1}-{case}-{clients}'
                            target = self.out / name
                            tail = ['--only', case, '--set', f'load.threads={clients}',
                                    '--set', 'load.warmup=15s', '--set', 'load.duration=60s', '--set', 'load.rampUp=5s',
                                    '--node', 'local', '--run-id', name, '--out', str(target)]
                            before = host_snapshot()
                            code = self.command(name, self.java('load', engine, tail))
                            raw = target / 'local.json'
                            self.report['runs'].append(dict(engine=engine, trial=trial + 1, case=f'{case}/{clients}',
                                returncode=code, report=str(raw.relative_to(self.out)) if raw.exists() else None,
                                host_before=before, host_after=host_snapshot()))
                            self.save()
                            # Fail the diagnostic rather than silently continuing
                            # after errors, missing reports or dropped scenarios.
                            read_run(self.out, self.report, self.report['runs'][-1])
        self.report['completed'] = True
        self.save()

    def publish(self):
        lines = [f'# CI selected-read diagnostic: {self.suite}', '',
                 '**Diagnostic only. Not eligible for merge or a general README advantage claim.**', '',
                 self.report['ci']['run_url'], '',
                 '| Original case / clients | RAMSQL QPS | PostgreSQL QPS | IRDB QPS |',
                 '| --- | ---: | ---: | ---: |']
        rows = []
        for case in self.report['expected_cases']:
            row = dict(case=case, engines={})
            for engine in ENGINES:
                runs = [run for run in self.report['runs'] if run['engine'] == engine and run['case'] == case]
                if len(runs) != 3 or {run['trial'] for run in runs} != {1, 2, 3}:
                    continue
                try:
                    values = [read_run(self.out, self.report, run) for run in runs]
                except (ValueError, KeyError, TypeError, OSError) as error:
                    self.report['gaps'].append(f'{engine}/{case}: {error}')
                    continue
                row['engines'][engine] = dict(median_qps=statistics.median(v['qps'] for v in values),
                    min_qps=min(v['qps'] for v in values), max_qps=max(v['qps'] for v in values),
                    median_p99_us=statistics.median(v['p99_us'] for v in values), trials=values)
            rows.append(row)
            lines.append('| ' + ' | '.join([case] + [f"{row['engines'][e]['median_qps']:.2f}" if e in row['engines'] else 'not scored' for e in ENGINES]) + ' |')
        lines += ['', 'Original full-suite correctness: ' + json.dumps(self.report['checks'], sort_keys=True),
                  '', 'Three trials per engine/case, original 5s ramp / 15s warmup / 60s measure; 1/8 clients. All trial ranges, p99, raw reports and hardware/pressure snapshots are in this artifact. Full-suite failures and capacity rejections remain blockers in the separate advantage gate.', '']
        lines += ['- ' + gap for gap in self.report['gaps']]
        if self.report.get('error'):
            lines += ['- ' + self.report['error']]
        (self.out / 'diagnostic-summary.json').write_text(json.dumps(dict(diagnostic_only=True, merge_allowed=False, rows=rows), indent=2) + '\n')
        text = '\n'.join(lines) + '\n'
        (self.out / 'diagnostic-summary.md').write_text(text)
        from evidence import publish
        publish(text)
        self.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=('sales-subset', 'sales-full'))
    args = parser.parse_args()
    campaign = SupportedCampaign(args.suite)
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
