"""CI-only qualification provenance and Linux runner diagnostics."""
import os
from pathlib import Path
import re
import subprocess


def validate_ci(context, revision):
    if not isinstance(context, dict):
        raise ValueError('Missing CI provenance object')
    if context.get('provider') != 'github-actions':
        raise ValueError('Performance qualification requires GitHub Actions provenance')
    for name in ('run_id', 'run_attempt'):
        if not re.fullmatch(r'[1-9][0-9]*', str(context.get(name, ''))):
            raise ValueError('Missing/invalid CI ' + name)
    if context.get('repository') != 'benor1470/dbMeter':
        raise ValueError('Unexpected benchmark repository')
    if not context.get('runner_name') or context.get('runner_os') != 'Linux':
        raise ValueError('Missing Linux runner identity')
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or context.get('checkout_revision') != revision:
        raise ValueError('CI checkout differs from input revision')


def validate_same_run(context, current):
    for key in ('repository', 'run_id', 'run_attempt', 'checkout_revision'):
        if context.get(key) != current[key]:
            raise ValueError('Campaign is outside this CI run/attempt/checkout: ' + key)


def actions_context(revision):
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Throughput campaigns run only in GitHub Actions; use local correctness/diagnostic tools instead')
    context = dict(provider='github-actions', checkout_revision=revision,
                   **{key: os.environ.get(env, '') for key, env in (
                       ('repository', 'GITHUB_REPOSITORY'), ('run_id', 'GITHUB_RUN_ID'),
                       ('run_attempt', 'GITHUB_RUN_ATTEMPT'), ('job', 'GITHUB_JOB'),
                       ('workflow', 'GITHUB_WORKFLOW'), ('event_sha', 'GITHUB_SHA'),
                       ('runner_name', 'RUNNER_NAME'), ('runner_os', 'RUNNER_OS'),
                       ('image_os', 'ImageOS'), ('image_version', 'ImageVersion'))})
    validate_ci(context, revision)
    context['run_url'] = f"https://github.com/{context['repository']}/actions/runs/{context['run_id']}/attempts/{context['run_attempt']}"
    return context


def host_snapshot():
    """Read bounded hardware/pressure metadata, never environment or command lines."""
    result = dict(cpu_affinity=sorted(os.sched_getaffinity(0)), files={})
    for name in ('/proc/meminfo', '/proc/loadavg', '/proc/pressure/cpu',
                 '/proc/pressure/memory', '/proc/pressure/io',
                 '/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current',
                 '/sys/fs/cgroup/cpu.max', '/sys/fs/cgroup/cpu.stat'):
        try:
            result['files'][name] = Path(name).read_text()[:16384]
        except OSError as error:
            result['files'][name] = dict(unavailable=type(error).__name__)
    try:
        cpu = subprocess.run(['lscpu', '--json'], capture_output=True, text=True, timeout=10)
        result['lscpu'] = dict(returncode=cpu.returncode, output=cpu.stdout[:32768])
    except (OSError, subprocess.TimeoutExpired) as error:
        result['lscpu'] = dict(unavailable=type(error).__name__)
    return result
