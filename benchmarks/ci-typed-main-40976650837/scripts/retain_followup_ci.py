"""Retain complete downloaded CI A/B campaigns using their own historical validator.

No engines or throughput measurements are run by this script.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REVIEWER = ROOT.parent / 'dbMeter'
EVIDENCE = ROOT.parent / 'ramsql-benchmark-evidence' / 'benchmarks'
SCRIPTS = ('paired_benchmark.py', 'evidence.py', 'live_benchmark.py',
           'runner.py', 'bootstrap.py', 'versions.json')


def main():
    run, revision, source_name, target_name, title = sys.argv[1:]
    source = REVIEWER / '.sig-cache' / source_name
    paired = source / 'paired-sales-subset'
    if not paired.exists():
        paired = source
    target = EVIDENCE / target_name
    assert not target.exists(), target
    scratch = REVIEWER / '.sig-cache' / ('historical-validator-' + run)
    scripts = scratch / 'ci'
    scripts.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        (scripts / name).write_bytes(subprocess.check_output(
            ['git', 'show', revision + ':ci/' + name], cwd=REVIEWER))
    # Import only the recorded revision's modules, including its immutable pins.
    sys.path.insert(0, str(scripts))
    import paired_benchmark
    campaign = json.loads((paired / 'campaign.json').read_text())
    assert campaign['completed'] and not campaign.get('error') and not campaign['gaps']
    assert campaign['ci']['run_id'] == run and campaign['ci']['run_attempt'] == '1'
    assert campaign['input_revision'] == revision and len(campaign['runs']) == 24
    summary = paired_benchmark.summarize_pairs(paired, campaign)
    assert summary == json.loads((paired / 'paired-summary.json').read_text())
    target.mkdir()
    for name, origin in [('paired-sales-subset', paired),
                         ('live-sales-subset', source / 'live-sales-subset')]:
        if not origin.exists():
            continue
        dest = target / name
        for path in sorted(origin.rglob('*')):
            if not path.is_file():
                continue
            if path.suffix not in ('.json', '.xml', '.csv') and path.name not in (
                    'summary.md', 'paired-summary.md', 'local-summary.txt'):
                continue
            output = dest / path.relative_to(origin)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
    shutil.copytree(scripts, target / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(__file__, target / 'scripts' / Path(__file__).name)
    lines = [f'# {title}: CI evidence', '',
        f'Source: [dbMeter run {run}, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/{run}/attempts/1).',
        'These are downloaded CI results, not local throughput measurements.', '',
        '## Matched comparison', '',
        'Same runner, AB / BA / AB, fresh original 1M data and all seven checks per variant/pair.',
        'Each original case retains its 5s ramp, 15s warmup and 60s measured window with a fresh JVM.',
        'The historical validator checks every one of the 24 raw reports, six check XMLs,',
        'binary provenance, shared dependency/toolchain/build arguments and scheduled order.', '',
        f'- dbMeter revision: `{revision}`.']
    for mode in ('baseline', 'candidate'):
        proof = campaign['paired_sources'][mode]
        lines.append(f'- {mode.title()}: `{proof["source"]["revision"]}`; binary SHA-256 `{proof["binary_sha256"]}`.')
    lines += ['', '| Original case / clients | Baseline median QPS | Candidate median QPS | Paired candidate/baseline ratios |',
              '| --- | ---: | ---: | --- |']
    for row in summary['rows']:
        engines = row['engines']
        ratios = ', '.join(f'{value:.3f}' for value in row['paired_qps_ratios'])
        lines.append(f'| {row["case"]} | {engines["baseline"]["median_qps"]:.2f} | {engines["candidate"]["median_qps"]:.2f} | {ratios} |')
    lines += ['', '## Limits of this evidence', '',
        'These measurements qualify a bounded execution improvement on the recorded workload.',
        'They do not establish full SQL compatibility, production readiness, 100M-row capacity,',
        'all-workload leadership or the all-engine dbMeter merge gate. Do not pool runners or',
        'attribute differences between separate workflow runs to a source change.', '',
        'The original full 5M suite still has four SQL-feature failures (3/7 checks pass).',
        'The dbMeter benchmark PR remains unmerged; no README advantage claim follows.', '',
        'All downloaded JSON/XML/CSV reports in each available campaign are retained, including',
        'individual trials, host observations and negative reference comparisons. Exact historical',
        'validation scripts and pins accompany the results. No vendor driver, executable, loader',
        'or source dataset is copied. `sha256.json` covers every retained file except itself.', '']
    (target / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    files = {path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(target.rglob('*')) if path.is_file()}
    (target / 'sha256.json').write_text(json.dumps(files, indent=2) + '\n', encoding='utf-8')
    print(f'Validated {run}: 24 original-duration cases / six original checks; retained {len(files)} hashed files.')


if __name__ == '__main__':
    main()
