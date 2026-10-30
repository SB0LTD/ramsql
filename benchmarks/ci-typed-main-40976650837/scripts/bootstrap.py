"""Checksum-verified benchmark dependencies; no benchmark engine code lives here."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / '.ci-runtime'
VERSIONS = json.loads((ROOT / 'ci/versions.json').read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(spec, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with urllib.request.urlopen(spec['url'], timeout=120) as response:
            target.write_bytes(response.read())
    if digest(target) != spec['sha256']:
        raise ValueError(f'Checksum mismatch for {target.name}')
    return target


def main():
    RUNTIME.mkdir(exist_ok=True)
    if sys.argv[1] in ('ramsql', 'ramsql-baseline'):
        runtime = RUNTIME if sys.argv[1] == 'ramsql' else RUNTIME / 'baseline-build'
        runtime.mkdir(exist_ok=True)
        spec = VERSIONS['ramsql' if sys.argv[1] == 'ramsql' else 'ramsql_baseline']
        source = download(spec, runtime / 'ramsql-source.tar.gz')
        sdk = download(VERSIONS['sig'], runtime / 'sig.tar.xz')
        for archive, name in [(source, 'source'), (sdk, 'sdk')]:
            folder = runtime / name
            folder.mkdir(exist_ok=False)
            with tarfile.open(archive) as contents:
                contents.extractall(folder, filter='data')
        compiler = next((runtime / 'sdk').rglob('bin/sig'))
        library = compiler.parent.parent / 'lib'
        checkout = next((runtime / 'source').iterdir())
        # Use the product's exact published no-libc runtime preparation, not
        # the older SDK runtime that requires libc and disables native workers.
        prepared = runtime / 'prepared-runtime'
        subprocess.run([sys.executable, str(checkout / 'tools/prepare_sig_runtime.py'),
                        '--installed-lib', str(library), '--output', str(prepared)], cwd=checkout, check=True)
        library = prepared / 'lib'
        env = dict(os.environ, SIG_LIB_DIR=str(library))
        build_arguments = ['build', '-Doptimize=ReleaseFast', '-Dtarget=x86_64-linux-musl', '-Dlink-libc=false']
        subprocess.run([str(compiler), *build_arguments, '--prefix', str(runtime / 'ramsql')], cwd=checkout, env=env, check=True)
        binary = runtime / 'ramsql/bin/ramsql'
        (runtime / 'ramsql-provenance.json').write_text(json.dumps(dict(
            source=spec, toolchain=VERSIONS['sig'], binary_sha256=digest(binary),
            dependency_manifest_sha256=digest(checkout / 'build.sig.zon'),
            runtime_preparation=json.loads((prepared / 'runtime-preparation.json').read_text()),
            capabilities=json.loads(subprocess.check_output([str(binary), '--capabilities'])),
            build_arguments=build_arguments), indent=2) + '\n')
    elif sys.argv[1] == 'dependencies':
        drivers = ROOT / 'drivers'
        drivers.mkdir(exist_ok=True)
        encoded = ''.join(os.environ.get(f'IRDB_JDBC_PART_{index}', '') for index in range(1, 6))
        if not encoded:
            raise ValueError('The supplied IRDB driver must be configured in the five private repository secrets; no synthetic IRDB results are allowed')
        jar = drivers / 'jirdb-2025-02-02.jar'
        jar.write_bytes(base64.b64decode(encoded, validate=True))
        if digest(jar) != VERSIONS['irdb_jdbc_sha256']:
            raise ValueError('Private IRDB client checksum mismatch')
        download(VERSIONS['postgres_jdbc'], drivers / 'postgresql-42.7.4.jar')
        download(VERSIONS['fastutil'], drivers / 'fastutil-8.5.15.jar')
        archive = download(VERSIONS['irdb'], RUNTIME / 'irdb.zip')
        with zipfile.ZipFile(archive) as contents:
            for member in contents.infolist():
                target = (RUNTIME / 'irdb' / member.filename).resolve()
                if not target.is_relative_to((RUNTIME / 'irdb').resolve()):
                    raise ValueError('Unsafe server archive path')
            contents.extractall(RUNTIME / 'irdb')
        subprocess.run(['docker', 'pull', VERSIONS['postgres_image']], check=True)
        image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', VERSIONS['postgres_image']]))[0]
        (RUNTIME / 'dependencies.json').write_text(json.dumps(dict(
            versions=VERSIONS, postgres_image_id=image['Id'], postgres_image_digests=image['RepoDigests'],
            java=subprocess.check_output(['java', '-version'], stderr=subprocess.STDOUT, text=True),
            dotnet=subprocess.check_output(['dotnet', '--info'], text=True)), indent=2) + '\n')
    else:
        raise ValueError('Expected ramsql or dependencies')


if __name__ == '__main__':
    main()
