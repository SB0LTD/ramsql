"""Prepare pinned Sig runtime/build support, verifying the released stdlib.

The compiler binary stays unchanged. The source pin is based on its 0.6.4
release; every installed standard-library source file must match byte-for-byte.
Only a new, caller-selected output directory is written. No toolchain install
or shared package cache is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import urllib.request
from package_release import runtime_provenance


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def extract_verified(archive,destination,pin):
    if sha(archive)!=pin['sha256']:raise ValueError('Sig runtime archive checksum mismatch')
    match=re.fullmatch(r'https://github\.com/SB0LTD/sig/archive/([0-9a-f]{40})\.tar\.gz',pin['archive'])
    if not match:raise ValueError('Sig runtime must be an immutable source archive')
    if destination.exists():raise ValueError('output directory already exists; choose a new directory')
    destination.mkdir(parents=True)
    prefix='sig-'+match[1]
    size=count=0
    with tarfile.open(archive,'r:gz') as source:
        for member in source:
            path=PurePosixPath(member.name)
            if path.is_absolute() or '\\' in member.name or '..' in path.parts or not path.parts or path.parts[0]!=prefix:
                raise ValueError('unsafe Sig runtime archive path')
            relative=path.parts[1:]
            if not relative or not (relative[0]=='lib' or relative[:2]==('tools','sig_build')):continue
            target=destination.joinpath(*relative)
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('archive escapes output directory')
            if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
            if not member.isfile():raise ValueError('runtime library/build support must contain only regular files')
            size+=member.size;count+=1
            if size>1024**3 or count>65536:raise ValueError('runtime extraction budget exceeded')
            target.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(member) as data,target.open('xb') as output:
                shutil.copyfileobj(data,output)
            if target.stat().st_size!=member.size:raise ValueError('truncated runtime source')
    if not (destination/'lib/sig/sig.sig').is_file() or not (destination/'tools/sig_build/main.sig').is_file():
        raise ValueError('missing public runtime or build runner')
    return destination/'lib'


def verify_stdlib(installed,prepared):
    expected=installed/'std';actual=prepared/'std'
    files=sorted(p for p in expected.rglob('*') if p.is_file() and p.suffix in ('.sig','.zig'))
    if not files or not (expected/'std.sig').is_file():raise ValueError('released standard library is missing')
    digest=hashlib.sha256()
    for path in files:
        relative=path.relative_to(expected)
        counterpart=actual/relative
        checksum=sha(path)
        if not counterpart.is_file() or sha(counterpart)!=checksum:
            raise ValueError('pinned source changes released standard library: '+relative.as_posix())
        digest.update(relative.as_posix().encode()+b'\0'+checksum.encode()+b'\n')
    return dict(matched_stdlib_files=len(files),matched_stdlib_sha256=digest.hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-lib',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive',type=Path,help='optional already-downloaded archive; checksum still verified')
    parser.add_argument('--github-env',type=Path,help='append SIG_LIB_DIR for subsequent workflow steps')
    args=parser.parse_args();pin=runtime_provenance()
    with tempfile.TemporaryDirectory(prefix='ramsql-runtime-download-') as directory:
        archive=args.archive
        if archive is None:
            archive=Path(directory)/'runtime.tar.gz'
            with urllib.request.urlopen(pin['archive'],timeout=60) as source,archive.open('xb') as output:
                shutil.copyfileobj(source,output)
        prepared=extract_verified(archive.resolve(strict=True),args.output.resolve(),pin)
    proof=dict(schema=1,sig_runtime=pin,**verify_stdlib(args.installed_lib.resolve(strict=True),prepared))
    proof_path=args.output.resolve()/'runtime-preparation.json'
    proof_path.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    if args.github_env:
        if any(c in str(prepared) for c in '\r\n'):raise ValueError('invalid environment path')
        with args.github_env.open('a',encoding='utf-8') as output:
            output.write(f'SIG_LIB_DIR={prepared}\nSIG_RUNTIME_PREPARATION={proof_path}\n')
    print(json.dumps(dict(library=str(prepared),**proof)),flush=True)


if __name__=='__main__':main()
