#!/usr/bin/env python3
"""Build real native SB0X/SB0K images from the explicit verified ZPM pin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def dependency():
    text = (ROOT / "build.sig.zon").read_text(encoding="utf-8")
    match = re.search(r'\.zpm\s*=\s*\.\{\s*\.url\s*=\s*"([^"]+)"\s*,\s*\.hash\s*=\s*"([0-9a-f]{64})"', text)
    if not match:
        raise RuntimeError("native releases require an explicit ZPM archive URL and SHA-256")
    url, digest = match.groups()
    revision = re.search(r"/archive/([0-9a-f]{40})\.tar\.gz$", url)
    if not revision:
        raise RuntimeError("ZPM archive URL must pin a full Git commit")
    return url, digest, revision.group(1)


def resolve_zpm(override=None):
    url, digest, revision = dependency()
    if override:
        source = Path(override).resolve()
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if actual != revision:
            raise RuntimeError(f"--zpm-root HEAD {actual} differs from manifest pin {revision}")
        tracked = subprocess.check_output(["git", "-C", str(source), "diff", "--name-only", "HEAD", "--", "src"], text=True).strip()
        if tracked:
            raise RuntimeError("--zpm-root has modified tracked source; build the verified archive instead")
        return source
    cache = ROOT / ".sig-cache" / "native-dependencies" / digest
    archive = cache / "zpm.tar.gz"
    source = cache / f"zpm-{revision}"
    cache.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        temporary = cache / "zpm.tar.gz.download"
        urllib.request.urlretrieve(url, temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise RuntimeError("ZPM archive SHA-256 mismatch")
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError("cached ZPM archive SHA-256 mismatch")
    if not source.exists():
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                target = (cache / member.name).resolve()
                if not target.is_relative_to(cache.resolve()) or member.issym() or member.islnk():
                    raise RuntimeError("unsafe path in dependency archive")
            tar.extractall(cache, filter="data")
    if not source.is_dir():
        raise RuntimeError("unexpected ZPM archive layout")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("sb0x", "sb0k"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zpm-root", type=Path, help="exact manifest-pinned clean ZPM checkout")
    parser.add_argument("--sig", default=os.environ.get("SIG", "sig"))
    parser.add_argument("--sig-lib-dir", type=Path, default=os.environ.get("SIG_LIB_DIR"))
    parser.add_argument("--optimize", choices=("Debug", "ReleaseSafe", "ReleaseFast", "ReleaseSmall"), default="ReleaseSmall")
    args = parser.parse_args()
    zpm = resolve_zpm(args.zpm_root)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".sig-cache" / "native-options" / args.format
    cache.mkdir(parents=True, exist_ok=True)
    options = cache / "options.sig"
    options.write_text(f"pub const kernel = {'true' if args.format == 'sb0k' else 'false'};\n", encoding="utf-8")
    command = [args.sig, "build-exe", "-target", "aarch64-sb0", "-mcpu=baseline", "-O" + args.optimize,
               "--stack", "8388608", "-fno-stack-check", "-fno-stack-protector", "-fno-unwind-tables", "-fstrip", "-ffunction-sections"]
    if args.sig_lib_dir:
        command += ["--Sig-lib-dir", str(args.sig_lib_dir.resolve())]
    if args.format == "sb0k":
        command += ["--script", str(ROOT / "src/platform/sb0k.ld")]
    for name in ("sql", "db_store", "postgres", "stream_server", "sb0_stream", "sb0_options"):
        command += ["--dep", name]
    command += [f"-Mroot={ROOT / 'src/platform/sb0_app.sig'}",
                f"-Msql={zpm / 'src/core/sql.sig'}", f"-Mdb_store={zpm / 'src/db/store.sig'}",
                f"-Mscram={zpm / 'src/db/scram.sig'}", "--dep", "scram",
                f"-Mpostgres={zpm / 'src/db/postgres.sig'}",
                f"-Mstream_server={zpm / 'src/db/stream_server.sig'}",
                f"-Msb0_stream={zpm / 'src/platform/sb0_stream.sig'}", f"-Msb0_options={options}",
                f"-femit-bin={output}"]
    version = subprocess.check_output([args.sig, "version"], text=True).strip()
    print(f"Building aarch64-sb0 native {args.format} using {version}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    image = output.read_bytes()
    expected = args.format.upper().encode("ascii")
    if len(image) < 64 or image[:4] != expected:
        raise RuntimeError(f"compiler did not emit a native {args.format.upper()} image")
    _, digest, revision = dependency()
    metadata = {"target": "aarch64-sb0", "format": args.format, "sig": version, "optimize": args.optimize,
                "zpm_revision": revision, "zpm_archive_sha256": digest,
                "image_sha256": hashlib.sha256(image).hexdigest(), "image_bytes": len(image),
                "capabilities": {"postgres_wire": True, "transport": "uart" if args.format == "sb0k" else "channel",
                                 "in_memory": True, "tcp": False, "durable_storage": False, "scram": False}}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Produced {output} ({len(image)} bytes), memory-only trusted transport", flush=True)


if __name__ == "__main__":
    main()
