"""Render all live results and fail closed before allowing a performance claim."""
import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import xml.etree.ElementTree as ET

from bootstrap import ROOT, VERSIONS
from live_benchmark import cases, PROFILES, ENGINES
from runner import validate_ci, actions_context, validate_same_run

SUITES = ('size-scaling', 'sales-subset', 'sales-full')
MIXED = {
    'sales-subset': {'s1_region', 's2_category', 's3_top_products', 's4_segment_discount', 's5_high_value_customers', 's6_store_category_mix'},
    'sales-full': {'q1_region_month', 'q2_category_rank', 'q3_top_products', 'q4_segment_discount_rollup', 'q5_high_value_customers', 'q6_store_category_mix'},
}
MIN_RATIO = 2.0
MAX_P99_RATIO = 1.10
CAPABILITY_CHECKS = {'cap_a_text_200_bytes', 'cap_b_9_columns', 'cap_c_20_more_tables'}


def check_xml(path, expected):
    root = ET.parse(path).getroot()
    nodes = list(root.iter('testcase'))
    if {node.attrib['name'] for node in nodes} != expected or len(nodes) != len(expected):
        raise ValueError('Original correctness cases were dropped or duplicated')
    if any(list(node) for node in nodes):
        raise ValueError('Original correctness cases failed or were skipped')


def percentile(histograms, percentage):
    """Same log-linear bucket midpoint convention as dbMeter's Histogram."""
    bins = defaultdict(int)
    count = 0
    minimum, maximum = None, 0
    for histogram in histograms:
        if histogram['subBits'] != 5 or sum(histogram['buckets'].values()) != histogram['count']:
            raise ValueError('Invalid histogram')
        count += histogram['count']
        minimum = histogram['min'] if minimum is None else min(minimum, histogram['min'])
        maximum = max(maximum, histogram['max'])
        for key, value in histogram['buckets'].items():
            if int(key) < 0 or value < 0:
                raise ValueError('Negative histogram value')
            bins[int(key)] += value
    if not count:
        raise ValueError('Empty latency histogram')
    rank, seen = math.ceil(percentage * count / 100), 0
    for index, size in sorted(bins.items()):
        seen += size
        if seen >= rank:
            low = index if index < 32 else (index % 32 + 32) << (index // 32 - 1)
            high = index if index < 32 else ((index % 32 + 33) << (index // 32 - 1)) - 1
            return max(minimum, min(maximum, (low + high) // 2))
    raise ValueError('Histogram count mismatch')


def read_run(folder, campaign, run):
    if run['returncode'] != 0 or not run.get('report'):
        raise ValueError('JDBC command failed or produced no report')
    path = (folder / run['report']).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError('Report escapes artifact folder')
    raw = json.loads(path.read_text())
    scenario, clients = run['case'].split('/')
    clients = int(clients)
    if raw.get('dbmeterReport') != 1 or len(raw['nodes']) != 1:
        raise ValueError('Expected one unmerged original dbMeter node')
    if raw['database']['label'] != run['engine']:
        raise ValueError('Actual JDBC report engine label differs from scheduled engine')
    settings = raw['nodes'][0]['settings']
    policy = PROFILES[campaign['profile']]
    expected = dict(threads=clients, duration='PT1M' if policy['duration'] == '60s' else 'PT3S',
                    warmup='PT15S' if policy['warmup'] == '15s' else 'PT2S',
                    rampUp='PT5S' if policy['ramp'] == '5s' else 'PT1S')
    if settings != expected:
        raise ValueError('JDBC phases or client count differ from declared policy')
    seconds = (raw['measuredEnd'] - raw['measuredStart']) / 1000
    requested = 60 if campaign['profile'] == 'qualification' else 3
    if not math.isfinite(seconds) or seconds < requested - .001:
        raise ValueError('Incomplete measured window')
    expected_scenarios = MIXED[campaign['suite']] if scenario == 'mixed' else {scenario}
    if set(raw['scenarios']) != expected_scenarios:
        raise ValueError('Workload scenarios were dropped or substituted')
    values = list(raw['scenarios'].values())
    for name, value in raw['scenarios'].items():
        if value['errors']:
            raise ValueError(f'Measured SQL errors in {name}: {value["errors"]}')
        if value['count'] <= 0:
            raise ValueError('No successful execution of declared scenario ' + name)
        if value['latencyMicros']['count'] != value['count']:
            raise ValueError('Inconsistent latency histogram in ' + name)
    return dict(qps=sum(value['count'] for value in values) / seconds,
                p99_us=percentile([value['latencyMicros'] for value in values], 99))


def summarize(folder):
    campaign = json.loads((folder / 'campaign.json').read_text())
    suite = campaign['suite']
    if suite not in SUITES or campaign['profile'] not in PROFILES:
        raise ValueError('Unknown suite/profile')
    expected = {f'{name}/{clients}' for name, clients in cases(suite)}
    if set(campaign['expected_cases']) != expected or campaign['policy'] != PROFILES[campaign['profile']]:
        raise ValueError('Declared benchmark matrix/policy changed')
    issues = list(campaign.get('gaps', []))
    if campaign.get('diagnostic_only'):
        issues.append('Selected-read diagnostics cannot qualify the all-case advantage gate')
    try:
        validate_ci(campaign.get('ci', {}), campaign['input_revision'])
    except (ValueError, TypeError, KeyError) as error:
        issues.append('CI provenance: ' + str(error))
    if campaign.get('error'):
        issues.append(campaign['error'])
    if campaign.get('completed') is not True:
        issues.append('Campaign did not complete')
    if campaign.get('checks') != dict.fromkeys(ENGINES, True):
        issues.append('Original correctness checks did not pass for all three engines')
    for engine in ENGINES:
        checks = ({f's{size}_{kind}' for size in (100, 512, 1000, 10000, 100000, 1000000) for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last')}
                  if suite == 'size-scaling' else {'a_totals'} | MIXED[suite])
        if suite == 'size-scaling' and engine == 'postgres':
            checks |= CAPABILITY_CHECKS
        try:
            check_xml(folder / (engine + '-checks.xml'), checks)
        except (OSError, ValueError, KeyError, ET.ParseError) as error:
            issues.append(engine + ' correctness evidence: ' + str(error))
    if suite == 'size-scaling' and campaign.get('ramsql_capability_probes_passed') is not True:
        issues.append('Original RAMSQL capability checks did not pass')
    if suite == 'size-scaling':
        try:
            check_xml(folder / 'ramsql-capabilities.xml', CAPABILITY_CHECKS)
        except (OSError, ValueError, KeyError, ET.ParseError) as error:
            issues.append('RAMSQL capability evidence: ' + str(error))
    if not campaign.get('original_input_sha256'):
        issues.append('Original input integrity manifest missing')
    if campaign['ramsql']['source'] != VERSIONS['ramsql']:
        issues.append('RAMSQL source differs from pinned candidate')
    grouped = defaultdict(list)
    seen = set()
    for run in campaign['runs']:
        key = (run['engine'], run['case'], run['trial'])
        if key in seen or run['engine'] not in ENGINES or run['case'] not in expected or not 1 <= run['trial'] <= campaign['policy']['trials']:
            issues.append('Unexpected/duplicate trial: ' + str(key))
            continue
        seen.add(key)
        try:
            grouped[(run['engine'], run['case'])].append(read_run(folder, campaign, run))
        except (KeyError, ValueError, OSError, TypeError) as error:
            issues.append(str(key) + ': ' + str(error))
    rows = []
    for case in sorted(expected):
        engines = {}
        for engine in ENGINES:
            trials = grouped[(engine, case)]
            if len(trials) != campaign['policy']['trials']:
                issues.append(f'{case}: missing valid {engine} trials')
                continue
            engines[engine] = dict(median_qps=statistics.median(t['qps'] for t in trials),
                                   min_qps=min(t['qps'] for t in trials), max_qps=max(t['qps'] for t in trials),
                                   median_p99_us=statistics.median(t['p99_us'] for t in trials))
        row = dict(case=case, engines=engines, advantage=False)
        if len(engines) == 3:
            ram = engines['ramsql']
            fastest = max(engines[e]['median_qps'] for e in ('postgres', 'irdb'))
            row['ratio_to_fastest'] = ram['median_qps'] / fastest
            row['advantage'] = (row['ratio_to_fastest'] >= MIN_RATIO
                                and ram['min_qps'] > max(engines[e]['max_qps'] for e in ('postgres', 'irdb'))
                                and all(ram['median_p99_us'] <= MAX_P99_RATIO * engines[e]['median_p99_us'] for e in ('postgres', 'irdb')))
        rows.append(row)
    qualifies = campaign['profile'] == 'qualification' and not issues and all(row['advantage'] for row in rows)
    return dict(schema=1, suite=suite, profile=campaign['profile'], qualified=qualifies,
                source_revision=campaign['ramsql']['source']['revision'], binary_sha256=campaign['ramsql']['binary_sha256'],
                input_revision=campaign['input_revision'], ci=campaign.get('ci', {}), issues=sorted(set(issues)), rows=rows)


def markdown(summary):
    lines = [f"## Live {summary['suite']} — {summary['profile']}", '',
             '**QUALIFIED significant advantage**' if summary['qualified'] else '**NOT QUALIFIED — keep the PR unmerged; no README performance claim.**', '',
             'All rates are queries/transactions per second at the original JDBC boundary. Bold ratios highlight at least 2× measured leads, including limited leads that do not pass the full gate.', '',
             '| Workload / clients | RAMSQL | PostgreSQL | IRDB | RAMSQL / fastest reference |',
             '| --- | ---: | ---: | ---: | ---: |']
    if summary.get('ci', {}).get('run_url'):
        lines[2:2] = [f"Evidence: {summary['ci']['run_url']}", '']
    for row in summary['rows']:
        cells = [row['case']]
        for engine in ENGINES:
            stats = row['engines'].get(engine)
            cells.append(f"{stats['median_qps']:,.2f}" if stats else 'not scored')
        ratio = row.get('ratio_to_fastest')
        cells.append(('**' if ratio >= MIN_RATIO else '') + f'{ratio:.2f}×' + ('**' if ratio >= MIN_RATIO else '') if ratio is not None else 'unqualified')
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines += ['', 'Gate: all original checks, all declared cases, all three engines, three 60-second trials; at least 2× the faster reference in each case, separated trial ranges, and median p99 no more than 10% worse than either reference. Smoke results never qualify.', '']
    if summary['issues']:
        lines += ['Gaps and invalid/missing evidence:', ''] + ['- ' + issue for issue in summary['issues']]
    return '\n'.join(lines) + '\n'


def publish(text):
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(text)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='verb', required=True)
    suite = sub.add_parser('suite')
    suite.add_argument('--suite', required=True, choices=SUITES)
    gate = sub.add_parser('gate')
    gate.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if args.verb == 'suite':
        folder = ROOT / 'reports/live' / args.suite
        folder.mkdir(parents=True, exist_ok=True)
        try:
            summary = summarize(folder)
        except (OSError, ValueError, KeyError, TypeError) as error:
            summary = dict(suite=args.suite, profile='unknown', qualified=False, rows=[], issues=['Incomplete/unreadable evidence: ' + str(error)])
        (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        text = markdown(summary)
        (folder / 'summary.md').write_text(text)
        publish(text)
    else:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        current_ci = actions_context(revision)
        summaries, errors = [], []
        folders = list(args.root.rglob('campaign.json'))
        suites_by_name = defaultdict(list)
        for path in folders:
            try:
                suites_by_name[json.loads(path.read_text())['suite']].append(path.parent)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append('Unreadable campaign artifact: ' + str(error))
        for name in SUITES:
            found = suites_by_name[name]
            if len(found) != 1:
                errors.append(name + ': exactly one complete artifact is required')
                continue
            try:
                summaries.append(summarize(found[0]))
            except (ValueError, KeyError, OSError, TypeError) as error:
                errors.append(name + ': ' + str(error))
        for key in ('source_revision', 'binary_sha256', 'input_revision'):
            if len({s[key] for s in summaries}) != 1:
                errors.append('Inconsistent candidate/input provenance: ' + key)
        for summary in summaries:
            try:
                validate_same_run(summary['ci'], current_ci)
            except ValueError as error:
                errors.append(str(error))
        passed = len(summaries) == 3 and not errors and all(s['qualified'] for s in summaries)
        result = dict(schema=1, qualified=passed, merge_allowed=passed, minimum_ratio=MIN_RATIO, max_p99_ratio=MAX_P99_RATIO,
                      workflow_run=f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
                      run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'), errors=errors, suites=summaries)
        (ROOT / 'reports').mkdir(exist_ok=True)
        (ROOT / 'reports/advantage.json').write_text(json.dumps(result, indent=2) + '\n')
        text = ('# Proven significant RAMSQL advantage\n' if passed else '# Advantage gate failed — do not merge or claim a win\n')
        text += '\n' + '\n'.join(markdown(s) for s in summaries)
        text += '\n'.join('- ' + error for error in errors) + '\n'
        (ROOT / 'reports/advantage.md').write_text(text)
        publish(text)
        if not passed:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
