"""Orchestrate real engines around unchanged dbMeter JDBC code and workloads."""
import argparse
import contextlib
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import time
import uuid

from bootstrap import ROOT, RUNTIME, VERSIONS, digest
from runner import actions_context, host_snapshot

ENGINES = ('ramsql', 'postgres', 'irdb')
SIZES = (100, 512, 1000, 10000, 100000, 1000000)
TABLES = ('regions', 'stores', 'categories', 'products', 'customers', 'sales')
PROFILES = {'smoke': dict(trials=1, warmup='2s', duration='3s', ramp='1s'),
            'qualification': dict(trials=3, warmup='15s', duration='60s', ramp='5s')}


def cases(suite):
    if suite == 'size-scaling':
        return [(f's{size}_{kind}', clients) for size in (100000, 1000000)
                for kind in ('pk', 'filter', 'topn') for clients in (1, 8)]
    return [('mixed', clients) for clients in (1, 8)]


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def wait_port(port, child=None):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        with socket.socket() as connection:
            connection.settimeout(.2)
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                return
        if child is not None and child.poll() is not None:
            raise RuntimeError('Owned database process exited before accepting connections')
        time.sleep(.1)
    raise TimeoutError('Owned database did not become ready')


def original_inputs():
    # New CI files do not alter the reviewer's Java/SQL/expected input files.
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().strip('\0').split('\0')
    hashes = {}
    for name in names:
        if not (name.startswith(('src/', 'irdb-jdbc/', 'examples/')) or name in ('build.sh', 'build.ps1')):
            continue
        actual = (ROOT / name).read_bytes().replace(b'\r\n', b'\n')
        expected = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT).replace(b'\r\n', b'\n')
        if actual != expected:
            raise ValueError('Original input was modified: ' + name)
        hashes[name] = hashlib.sha256(actual).hexdigest()
    return hashes


def overlay(original, output, updates):
    lines = []
    for line in original.read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            lines.append(line)
            continue
        key, value = line.split('=', 1)
        if key in updates:
            value = updates.pop(key)
        elif key.endswith(('.file', '.expect.csv')):
            value = (original.parent / value).resolve().as_posix()
        lines.append(key + '=' + value)
    lines += [key + '=' + value for key, value in updates.items()]
    output.write_text('\n'.join(lines) + '\n')
    return output


def fresh_seed(original, target):
    """Only omit unconditional DROP preludes for a provably fresh server.

    Original examples intentionally continue after these missing-table errors.
    Every CREATE/INSERT remains byte-for-byte and ON_ERROR_STOP stays enabled.
    """
    allowed = set(TABLES) | {f't_{size}' for size in SIZES}
    omitted, preamble = [], True
    with original.open('rb') as source, target.open('wb') as output:
        for line in source:
            match = re.fullmatch(rb'DROP TABLE ([a-z_0-9]+);\s*', line)
            if match:
                table = match[1].decode('ascii')
                if not preamble or table not in allowed:
                    raise ValueError('Unexpected DROP outside the known fresh-dataset prelude')
                omitted.append(table)
                continue
            if line.strip() and not line.lstrip().startswith(b'--'):
                preamble = False
            output.write(line)
    return omitted


class Campaign:
    def __init__(self, suite, profile):
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        ci_context = actions_context(revision)
        self.suite, self.profile = suite, profile
        self.policy = PROFILES[profile]
        self.out = ROOT / 'reports/live' / suite
        self.out.mkdir(parents=True, exist_ok=False)
        self.work = RUNTIME / suite
        self.work.mkdir(parents=True, exist_ok=False)
        self.port = {}
        for engine in ENGINES:
            port = free_port()
            while port in self.port.values():
                port = free_port()
            self.port[engine] = port
        self.container = 'dbmeter-' + uuid.uuid4().hex[:12]
        self.pg_created = False
        self.binary = RUNTIME / 'downloaded-ramsql/ramsql/bin/ramsql'
        self.binary.chmod(0o755)
        self.ram_provenance = json.loads((RUNTIME / 'downloaded-ramsql/ramsql-provenance.json').read_text())
        if digest(self.binary) != self.ram_provenance['binary_sha256']:
            raise ValueError('Downloaded RAMSQL artifact changed')
        self.folder = ROOT / 'examples' / {'size-scaling': 'size-scaling', 'sales-subset': 'sales-agg-subset', 'sales-full': 'sales-agg'}[suite]
        self.configs = {}
        self.data_files = []
        self.report = dict(schema=1, suite=suite, profile=profile, policy=self.policy,
                           expected_cases=[f'{name}/{clients}' for name, clients in cases(suite)],
                           input_revision=revision, ci=ci_context,
                           environment=dict(platform=platform.platform(), logical_cpus=os.cpu_count(),
                                            memory_total_bytes=os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES'),
                                            runner=host_snapshot()),
                           ramsql=self.ram_provenance, dependencies=json.loads((RUNTIME / 'dependencies.json').read_text()),
                           scope='memory-resident read-only JDBC workloads; not durable-commit or 100M-row qualification',
                           checks={}, runs=[], gaps=[], commands=[])
        self.save()

    def save(self):
        (self.out / 'campaign.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def command(self, name, argv, *, stdin=None, timeout=600, env=None):
        self.report['commands'].append(dict(name=name, argv=list(map(str, argv))))
        self.save()
        with (self.out / (name + '.log')).open('wb') as log:
            result = subprocess.run(list(map(str, argv)), cwd=ROOT, stdin=stdin, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout, env=env)
        return result.returncode

    def java(self, verb, engine, tail=()):
        selected = []
        if verb == 'check' and self.suite == 'size-scaling' and engine != 'postgres':
            selected = ['--only', ','.join(f's{n}_{kind}' for n in SIZES for kind in ('a_pk', 'b_filter', 'c_topn', 'd_last'))]
        return ['java', '-Xms32m', '-Xmx256m', '-jar', str(ROOT / 'build/dbmeter.jar'), verb,
                '--skip-setup', '-c', str(self.configs[engine]), '--set', 'run.databaseLabel=' + engine,
                *selected, *tail]

    @contextlib.contextmanager
    def running(self, engine):
        if engine == 'postgres':
            if not self.pg_created:
                subprocess.run(['docker', 'create', '--name', self.container,
                                '--network', 'host', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                                '-e', f'PGPORT={self.port[engine]}', VERSIONS['postgres_image'],
                                'postgres', '-c', f'port={self.port[engine]}', '-c', 'listen_addresses=127.0.0.1'],
                               check=True, stdout=subprocess.DEVNULL)
                self.pg_created = True
            subprocess.run(['docker', 'start', self.container], check=True, stdout=subprocess.DEVNULL)
            try:
                wait_port(self.port[engine])
                # A listening socket can precede the final PostgreSQL startup.
                # Bootstrap's temporary postmaster accepts Unix sockets only.
                # Require TCP, matching JDBC, before declaring the engine ready.
                for attempt in range(600):
                    if subprocess.run(['docker', 'exec', self.container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('PostgreSQL did not finish initialization')
                yield
            finally:
                with (self.out / 'postgres-server.log').open('ab') as log:
                    subprocess.run(['docker', 'logs', self.container], stdout=log, stderr=subprocess.STDOUT, check=True)
                subprocess.run(['docker', 'stop', '-t', '30', self.container], check=True, stdout=subprocess.DEVNULL)
        else:
            if engine == 'ramsql':
                banks = list(SIZES) + [1, 1] if self.suite == 'size-scaling' else [5, 200, 20, 1000, 50000, 1000000 if self.suite == 'sales-subset' else 5000000, 1, 1]
                versions = [max(2, n * 2) for n in banks]
                if self.suite == 'sales-full':
                    versions[5] = banks[5] + 65536
                workers = min(8, len(os.sched_getaffinity(0)))
                argv = [str(self.binary), '--serve', '--memory', '--insecure-local', '--port', str(self.port[engine]),
                        '--workers', str(workers), '--max-clients', '12', '--idle-ms', '300000',
                        '--table-rows', ','.join(map(str, banks)), '--table-versions', ','.join(map(str, versions)),
                        '--work-rows', '4096' if self.suite == 'size-scaling' else '65536', '--result-rows', '2048']
                self.report['ramsql_server_arguments'] = argv[1:]
                capacity_flags = ('--workers', '--max-clients', '--table-rows', '--table-versions', '--work-rows', '--result-rows')
                probe = [str(self.binary), '--machine-capacity']
                for flag in capacity_flags:
                    index = argv.index(flag)
                    probe += argv[index:index + 2]
                self.report['ramsql_machine_capacity'] = json.loads(subprocess.check_output(probe, text=True, timeout=30))
                self.save()
            else:
                server = next((RUNTIME / 'irdb').rglob('irdbServerConsole.dll'))
                ini = self.work / 'irdb.ini'
                # Fresh synthetic CI data and vendor's public default lab
                # password; never copy the reviewer's supplied private INI.
                ini.write_text('[general]\ndata_dir=' + (self.work / 'irdb-data').as_posix() + '\nport=' + str(self.port[engine]) + '\npassword=!TopSecret\nlog_dir=' + (self.work / 'irdb-logs').as_posix() + '\nnocache=true\n')
                (self.work / 'irdb-data').mkdir(exist_ok=True)
                (self.work / 'irdb-logs').mkdir(exist_ok=True)
                argv = ['dotnet', str(server), str(ini)]
            log = (self.out / (engine + '-server.log')).open('ab')
            child = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                wait_port(self.port[engine], child)
                yield
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=15)
                log.close()

    def prepare_configs(self):
        for engine in ENGINES:
            name = 'postgres.properties' if self.suite == 'sales-full' and engine == 'postgres' else 'pg.properties' if engine == 'postgres' else engine + '.properties'
            original = self.folder / name
            changes = dict()
            if engine == 'irdb' and self.suite == 'sales-subset':
                original = self.folder / 'ramsql.properties'
                # Existing full-suite IRDB dialect uses ISO strings instead of
                # DATE literals. This is the only subset SQL adaptation.
                query = self.folder / 'sql/s6_store_category_mix.sql'
                text = query.read_text().replace("DATE '2025-03-01'", "'2025-03-01'").replace("DATE '2025-08-31'", "'2025-08-31'")
                adapted = self.work / 'irdb-store-category.sql'
                adapted.write_text(text)
                changes.update({'driver.jar': ','.join((ROOT / p).as_posix() for p in ('build/irdb-jdbc.jar', 'drivers/jirdb-2025-02-02.jar', 'drivers/fastutil-8.5.15.jar')),
                                'check.s6_store_category_mix.file': adapted.as_posix(), 'scenario.s6_store_category_mix.file': adapted.as_posix()})
                self.report['irdb_subset_adaptation'] = dict(original_sha256=digest(query), adapted_sha256=digest(adapted), change='Remove only the DATE keyword from two ISO literals, matching original full-suite IRDB dialect; expected CSV unchanged')
            changes['jdbc.url'] = f'jdbc:irdb://127.0.0.1:{self.port[engine]}/live_{self.suite.replace("-", "_")}' if engine == 'irdb' else f'jdbc:postgresql://127.0.0.1:{self.port[engine]}/' + ('ramsql' if engine == 'ramsql' else 'postgres')
            changes['jdbc.user'] = 'default' if engine == 'irdb' else 'postgres'
            changes['jdbc.password'] = '${env:IRDB_PASSWORD:-!TopSecret}' if engine == 'irdb' else ''
            # Adapted configs live outside examples, so resolve all drivers too.
            if 'driver.jar' not in changes:
                changes['driver.jar'] = ','.join((ROOT / p).as_posix() for p in (('build/irdb-jdbc.jar', 'drivers/jirdb-2025-02-02.jar', 'drivers/fastutil-8.5.15.jar') if engine == 'irdb' else ('drivers/postgresql-42.7.4.jar',)))
            self.configs[engine] = overlay(original, self.work / (engine + '.properties'), changes)

    def psql(self, source):
        prepared = self.work / ('fresh-' + source.name)
        omitted = fresh_seed(source, prepared)
        self.report.setdefault('fresh_load_preludes', {})[source.name] = dict(
            omitted_missing_table_drops=omitted, original_sha256=digest(source), transfer_sha256=digest(prepared))
        argv = ['docker', 'run', '--rm', '--network', 'host', '-i', '--entrypoint', 'psql', VERSIONS['postgres_image'],
                '-h', '127.0.0.1', '-p', str(self.port['ramsql']), '-U', 'postgres', '-d', 'ramsql', '-q', '-v', 'ON_ERROR_STOP=1']
        with prepared.open('rb') as stream:
            if self.command('ramsql-seed-' + source.stem, argv, stdin=stream):
                raise RuntimeError('Real SQL data transfer failed')

    def export_full(self):
        output = self.work / 'ramsql-full.sql'
        with output.open('w') as sql:
            definitions = {'regions': 'id INT PRIMARY KEY,name TEXT NOT NULL', 'stores': 'id INT PRIMARY KEY,region_id INT NOT NULL,name TEXT NOT NULL',
                           'categories': 'id INT PRIMARY KEY,name TEXT NOT NULL', 'products': 'id INT PRIMARY KEY,category_id INT NOT NULL,unit_price_cents INT NOT NULL',
                           'customers': 'id INT PRIMARY KEY,segment TEXT NOT NULL',
                           'sales': 'id BIGINT PRIMARY KEY,product_id INT NOT NULL,store_id INT NOT NULL,customer_id INT NOT NULL,sale_date DATE NOT NULL,quantity INT NOT NULL,discount_pct INT NOT NULL,amount_cents BIGINT NOT NULL'}
            quoted = {'regions': {1}, 'stores': {2}, 'categories': {1}, 'products': set(), 'customers': {1}, 'sales': {4}}
            expected_counts = dict(zip(TABLES, (5, 200, 20, 1000, 50000, 5000000)))
            hashes = {}
            for table in TABLES:
                path = self.work / (table + '.csv')
                with path.open('wb') as stream:
                    subprocess.run(['docker', 'exec', self.container, 'psql', '-U', 'postgres', '-d', 'postgres', '-q', '-v', 'ON_ERROR_STOP=1', '-c',
                                    f'COPY (SELECT * FROM dbmeter_agg.{table} ORDER BY id) TO STDOUT WITH CSV HEADER'], stdout=stream, check=True)
                sql.write(f'CREATE TABLE {table} ({definitions[table]});\n')
                count = 0
                batch = []
                with path.open(newline='') as stream:
                    rows = csv.reader(stream)
                    next(rows)
                    for row in rows:
                        # Full schema has no NULLs. Preserve CSV values exactly;
                        # the original independent expected CSV verifies data.
                        if any(value == '' for value in row):
                            raise ValueError('Unexpected NULL in the original full dataset')
                        values = ["'" + value.replace("'", "''") + "'" if i in quoted[table] else str(int(value)) for i, value in enumerate(row)]
                        batch.append('(' + ','.join(values) + ')')
                        count += 1
                        if len(batch) == 50:
                            sql.write(f'INSERT INTO {table} VALUES ' + ','.join(batch) + ';\n')
                            batch.clear()
                    if batch:
                        sql.write(f'INSERT INTO {table} VALUES ' + ','.join(batch) + ';\n')
                if count != expected_counts[table]:
                    raise ValueError(f'{table}: expected {expected_counts[table]} actual rows, got {count}')
                hashes[table] = dict(rows=count, sha256=digest(path))
        self.report['full_data_transfer'] = dict(tables=hashes, sql_sha256=digest(output))
        return output

    def setup(self):
        self.prepare_configs()
        with self.running('postgres'):
            # Only the original PostgreSQL config performs setup. RAMSQL's
            # full-suite alpha.2 sample schema is deliberately bypassed.
            command = self.java('check', 'postgres', ['--junit', str(self.out / 'postgres-checks.xml')])
            command.remove('--skip-setup')
            if self.command('postgres-checks', command):
                raise RuntimeError('Original PostgreSQL correctness checks failed')
            self.report['checks']['postgres'] = True
            if self.suite == 'sales-full':
                self.data_files = [self.export_full()]
            elif self.suite == 'sales-subset':
                self.data_files = [ROOT / 'build/sales-agg-subset/ramsql_load.sql']
            else:
                self.data_files = [ROOT / f'build/size-scaling/ramsql_load_t_{size}.sql' for size in SIZES]
            # CopyToIrdb is the original adapter utility, not a replacement DB.
            with self.running('irdb'):
                schema = {'size-scaling': 'dbmeter_size', 'sales-subset': 'dbmeter_agg_subset', 'sales-full': 'dbmeter_agg'}[self.suite]
                tables = ','.join(f't_{size}' for size in SIZES) if self.suite == 'size-scaling' else ','.join(TABLES)
                classpath = os.pathsep.join(str(ROOT / p) for p in ('build/irdb-jdbc.jar', 'drivers/jirdb-2025-02-02.jar', 'drivers/fastutil-8.5.15.jar', 'drivers/postgresql-42.7.4.jar'))
                argv = ['java', '-Xms32m', '-Xmx256m', '-cp', classpath, 'dbmeter.irdb.CopyToIrdb', '--source',
                        f'jdbc:postgresql://127.0.0.1:{self.port["postgres"]}/postgres', '--source-user', 'postgres', '--source-schema', schema,
                        '--target', f'jdbc:irdb://127.0.0.1:{self.port["irdb"]}/live_{self.suite.replace("-", "_")}', '--tables', tables, '--chunk', '100000']
                if self.command('irdb-copy', argv):
                    raise RuntimeError('Original IRDB transfer failed')
        # Setup may have two databases resident; no timing occurs until both
        # stop. During timed runs only the selected engine is active.
        for engine in ('irdb', 'ramsql'):
            try:
                self.check_engine(engine)
            except RuntimeError:
                log = self.out / 'ramsql-server.log'
                capacity = engine == 'ramsql' and log.exists() and any(
                    message in log.read_text(errors='replace') for message in ('error: BudgetExceeded', 'error: MemoryReservationFailed'))
                if not capacity:
                    raise
                self.report['checks'][engine] = False
                self.report['gaps'].append('RAMSQL native capacity admission rejected the actual runner/banks; no fixed RAM threshold, sample data or smaller build substituted. See machine budget and server log.')
        self.report['original_input_sha256'] = original_inputs()
        self.save()

    def check_engine(self, engine):
        with self.running(engine):
            if engine == 'ramsql':
                for source in self.data_files:
                    self.psql(source)
            passed = self.command(engine + '-checks', self.java('check', engine, ['--junit', str(self.out / (engine + '-checks.xml'))])) == 0
            self.report['checks'][engine] = passed
            if not passed:
                self.report['gaps'].append(engine + ' failed original full-suite correctness; no throughput scored for this suite')
            if engine == 'ramsql' and self.suite == 'size-scaling':
                probes = [line.split('.', 2)[1] for line in (self.folder / 'ramsql.properties').read_text().splitlines() if line.startswith('check.cap_') and '.sql=' in line]
                # The original capability probes remain explicit merge
                # blockers. Only the unscored setup instance runs them.
                argv = ['java', '-Xms32m', '-Xmx256m', '-jar', str(ROOT / 'build/dbmeter.jar'), 'check', '--skip-setup', '-c', str(self.configs[engine]), '--only', ','.join(probes), '--junit', str(self.out / 'ramsql-capabilities.xml')]
                self.report['ramsql_capability_probes_passed'] = self.command('ramsql-capabilities', argv) == 0
                if not self.report['ramsql_capability_probes_passed']:
                    self.report['gaps'].append('Original RAMSQL text/column/table capability probes failed; valid read timings do not close those gaps')

    def run(self):
        generator = self.folder / ('gen_expected.py' if self.suite == 'sales-full' else 'gen.py')
        if self.command('original-generator', [sys.executable, '-I', str(generator)]):
            raise RuntimeError('Original generator failed')
        self.setup()
        if not all(self.report['checks'].get(engine, False) for engine in ENGINES):
            return
        for trial in range(self.policy['trials']):
            order = ENGINES[trial % 3:] + ENGINES[:trial % 3]
            for engine in order:
                with self.running(engine):
                    if engine == 'ramsql':
                        for source in self.data_files:
                            self.psql(source)
                        if self.command(f'ramsql-trial{trial + 1}-checks', self.java('check', engine)):
                            raise RuntimeError('Fresh RAMSQL data failed correctness')
                    for scenario, clients in cases(self.suite):
                        name = f'{engine}-trial{trial + 1}-{scenario}-{clients}'
                        target = self.out / name
                        tail = ['--set', f'load.threads={clients}', '--set', 'load.warmup=' + self.policy['warmup'],
                                '--set', 'load.duration=' + self.policy['duration'], '--set', 'load.rampUp=' + self.policy['ramp'],
                                '--node', 'local', '--run-id', name, '--out', str(target)]
                        if scenario != 'mixed':
                            tail += ['--only', scenario]
                        before = host_snapshot()
                        code = self.command(name, self.java('load', engine, tail))
                        raw = target / 'local.json'
                        self.report['runs'].append(dict(engine=engine, trial=trial + 1, case=f'{scenario}/{clients}',
                                                        returncode=code, report=str(raw.relative_to(self.out)) if raw.exists() else None,
                                                        host_before=before, host_after=host_snapshot()))
                        self.save()
        self.report['completed'] = True
        self.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', required=True, choices=('size-scaling', 'sales-subset', 'sales-full'))
    parser.add_argument('--profile', required=True, choices=PROFILES)
    args = parser.parse_args()
    campaign = Campaign(args.suite, args.profile)
    try:
        campaign.run()
    except BaseException as error:
        campaign.report['error'] = type(error).__name__ + ': ' + str(error)
        campaign.save()
        raise


if __name__ == '__main__':
    main()
