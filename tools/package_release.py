"""Build deterministic release archives and verify the complete target manifest.

Only Python's standard library is needed. Runtime RAMSQL remains pure Sig.
Every archive includes exact source/toolchain/dependency provenance and explicit
platform capabilities. A successful package does not bypass release_gate.py.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import struct
import tarfile
import zipfile

from release_matrix import ROOT, SIG_ASSET, SIG_SHA256, SIG_TAG, TARGETS, source_version, target

HOSTED_RUNTIME_TESTS = ["tools/test_cli.py", "tools/test_server.py", "tools/test_clients.py",
                        "tools/test_auth.py", "tools/test_persistence.py"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(data: object) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def validate_binary(data: bytes, entry: dict) -> None:
    """Reject wrong-OS/architecture artifacts, including Linux in an SB0 slot."""
    fmt, arch = entry["format"], entry["arch"]
    if fmt == "elf":
        if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
            raise ValueError("expected a 64-bit little-endian ELF executable")
        if struct.unpack_from("<H", data, 16)[0] not in (2, 3) or struct.unpack_from("<I", data, 20)[0] != 1:
            raise ValueError("expected an ELF executable or position-independent executable")
        if struct.unpack_from("<H", data, 18)[0] != {"x86_64": 62, "aarch64": 183}[arch]:
            raise ValueError("ELF architecture differs from target")
    elif fmt == "macho":
        if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("expected a 64-bit little-endian Mach-O executable")
        if struct.unpack_from("<I", data, 12)[0] != 2:
            raise ValueError("expected a Mach-O executable, not an object or library")
        if struct.unpack_from("<I", data, 4)[0] != {"x86_64": 0x01000007, "aarch64": 0x0100000C}[arch]:
            raise ValueError("Mach-O architecture differs from target")
    elif fmt == "pe":
        if len(data) < 64 or data[:2] != b"MZ":
            raise ValueError("expected a PE executable")
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if offset + 24 > len(data) or data[offset:offset + 4] != b"PE\0\0":
            raise ValueError("invalid PE header")
        if struct.unpack_from("<H", data, offset + 4)[0] != {"x86_64": 0x8664, "aarch64": 0xAA64}[arch]:
            raise ValueError("PE architecture differs from target")
        optional_bytes, characteristics = struct.unpack_from("<HH", data, offset + 20)
        if optional_bytes < 112 or offset + 24 + optional_bytes > len(data) or not characteristics & 2:
            raise ValueError("expected a PE executable with a complete optional header")
        if struct.unpack_from("<H", data, offset + 24)[0] != 0x20B:
            raise ValueError("expected a PE32+ executable")
    elif fmt in ("sb0x", "sb0k"):
        if len(data) < 64 or data[:4] != fmt.upper().encode():
            raise ValueError(f"expected native {fmt.upper()} image, not a hosted executable")
    else:
        raise ValueError(f"unsupported binary format: {fmt}")


def dependency_provenance() -> dict:
    zon = (ROOT / "build.sig.zon").read_text(encoding="utf-8")
    url = re.search(r'\.url\s*=\s*"([^"]+)"', zon)
    digest = re.search(r'\.hash\s*=\s*"([0-9a-f]{64})"', zon)
    if not url or not digest or not re.fullmatch(r"https://github\.com/SB0LTD/zpm/archive/[0-9a-f]{40}\.tar\.gz", url[1]):
        raise ValueError("release requires an immutable, SHA-256-pinned ZPM archive")
    return {"archive": url[1], "sha256": digest[1]}


def capabilities(entry: dict) -> dict:
    hosted_server = entry["kind"] == "hosted"
    return {
        "runtime": entry["runtime"], "image_format": entry["format"],
        "allocator_free_sig": True,
        "hosted_tcp_server": hosted_server,
        "hosted_durable_wal": hosted_server,
        "native_sb0": entry["kind"] == "native-sb0",
        "native_transport": {"sb0s": "SB0 channel", "sb0k": "PL011 UART"}.get(entry["runtime"]),
        "native_authentication": "trusted transport, memory only" if entry["kind"] == "native-sb0" else None,
        "production_postgres_drop_in": False,
        "qualification": "See RELEASE.md and the attached test evidence; compilation alone is not runtime validation.",
    }


def published_toolchain() -> dict:
    return {"kind": "published-release", "release": SIG_TAG,
            "asset": SIG_ASSET, "sha256": SIG_SHA256}


def validate_toolchain(provenance: dict, entry: dict) -> None:
    if provenance.get("kind") == "published-release":
        if provenance != published_toolchain():
            raise ValueError("published compiler provenance differs from verified release")
        return
    if entry["kind"] != "native-sb0" or provenance.get("kind") != "source-build":
        raise ValueError("only native SB0 artifacts may use the pinned source-built compiler")
    revision = provenance.get("source_revision", "")
    if (provenance.get("schema") != 1 or provenance.get("source_repository") != "https://github.com/SB0LTD/sig"
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or provenance.get("library_revision") != revision
            or not re.fullmatch(r"[0-9a-f]{64}", provenance.get("compiler_sha256", ""))
            or not isinstance(provenance.get("compiler_version"), str)
            or not 1 <= len(provenance["compiler_version"]) <= 256
            or provenance.get("bootstrap") != published_toolchain()):
        raise ValueError("source-built compiler requires exact source, library, binary and bootstrap provenance")


def create_archive(path: Path, prefix: str, files: dict[str, tuple[bytes, int]], epoch: int) -> None:
    """Stable ordering, modes and timestamps; never include local filesystem metadata."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, (data, mode) in sorted(files.items()):
                info = zipfile.ZipInfo(f"{prefix}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100000 | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
    else:
        with path.open("xb") as output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for name, (data, mode) in sorted(files.items()):
                        info = tarfile.TarInfo(f"{prefix}/{name}")
                        info.size, info.mode, info.mtime = len(data), mode, epoch
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, io.BytesIO(data))


def package(args: argparse.Namespace) -> dict:
    entry = target(args.target)
    version = source_version()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("revision must be the complete source commit SHA")
    if not 0 <= args.source_date_epoch <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH must fit the gzip timestamp")
    binary = args.binary.read_bytes()
    validate_binary(binary, entry)
    toolchain = published_toolchain()
    if getattr(args, "toolchain_manifest", None):
        toolchain = json.loads(args.toolchain_manifest.read_text(encoding="utf-8"))
    validate_toolchain(toolchain, entry)
    provenance = {
        "schema": 1, "project": "ramsql", "version": version,
        "source": {"repository": "https://github.com/SB0LTD/ramsql", "revision": args.revision},
        "source_date_epoch": args.source_date_epoch,
        "target": entry, "optimize": args.optimize,
        "toolchain": toolchain,
        "zpm": dependency_provenance(), "capabilities": capabilities(entry),
        "verification": {"binary_header": "passed", "cross_compilation": "passed", "runtime": "not_claimed_by_packaging"},
    }
    files = {
        entry["binary"]: (binary, 0o755),
        "README.md": ((ROOT / "README.md").read_bytes(), 0o644),
        "LICENSE": ((ROOT / "LICENSE").read_bytes(), 0o644),
        "RELEASE.md": ((ROOT / "compatibility/RELEASE.md").read_bytes(), 0o644),
        "release-gates.json": ((ROOT / "compatibility/release-gates.json").read_bytes(), 0o644),
        "postgres-report.json": ((ROOT / "compatibility/postgres-report.json").read_bytes(), 0o644),
        "provenance.json": (json_bytes(provenance), 0o644),
    }
    if args.verification:
        report = json.loads(args.verification.read_text(encoding="utf-8"))
        if report.get("image_sha256") != sha256(binary) or report.get("passed") is not True:
            raise ValueError("runtime evidence must pass and identify this exact image SHA-256")
        provenance["verification"]["runtime"] = "passed"
        provenance["verification"]["evidence"] = "runtime-test.json"
        files["runtime-test.json"] = (json_bytes(report), 0o644)
        files["provenance.json"] = (json_bytes(provenance), 0o644)
    contents = [{"path": name, "sha256": sha256(data), "bytes": len(data), "mode": oct(mode)}
                for name, (data, mode) in sorted(files.items())]
    files["contents.json"] = (json_bytes({"schema": 1, "files": contents}), 0o644)
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = f"ramsql-{version}-{entry['slug']}"
    extension = ".zip" if entry["format"] == "pe" else ".tar.gz"
    archive_path = args.output / (prefix + extension)
    create_archive(archive_path, prefix, files, args.source_date_epoch)
    outputs = [{"file": archive_path.name, "sha256": sha256(archive_path.read_bytes()), "bytes": archive_path.stat().st_size}]
    if entry["kind"] == "native-sb0":
        raw = args.output / entry["binary"]
        with raw.open("xb") as output:
            output.write(binary)
        outputs.append({"file": raw.name, "sha256": sha256(binary), "bytes": len(binary)})
    record = {"schema": 1, "version": version, "revision": args.revision, "target": entry["slug"],
              "provenance": provenance, "artifacts": outputs}
    (args.output / f"artifact-{entry['slug']}.json").write_bytes(json_bytes(record))
    return record


def archive_contents(path: Path, prefix: str) -> dict[str, tuple[bytes, int]]:
    """Read only regular flat files under the expected package root; no extraction."""
    files = {}
    total = 0
    maximum = 512 * 1024 * 1024
    def accept(name: str, size: int, mode: int, read):
        nonlocal total
        if not name.startswith(prefix + "/"):
            raise ValueError("archive file is outside the expected package root")
        relative = name[len(prefix) + 1:]
        if not relative or "/" in relative or "\\" in relative or relative in (".", "..") or relative in files:
            raise ValueError("archive has an invalid or duplicate file path")
        total += size
        if size < 0 or total > maximum or len(files) >= 32:
            raise ValueError("archive contents exceed release package limits")
        data = read()
        if len(data) != size:
            raise ValueError("archive file length mismatch")
        files[relative] = (data, mode & 0o777)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                mode = member.external_attr >> 16
                if member.is_dir() or mode & 0o170000 != 0o100000:
                    raise ValueError("archive may contain only regular files")
                accept(member.filename, member.file_size, mode, lambda member=member: archive.read(member))
    else:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    raise ValueError("archive may contain only regular files")
                accept(member.name, member.size, member.mode,
                       lambda member=member: archive.extractfile(member).read())
    return files


def verify_package(directory: Path, slug: str, revision: str) -> dict:
    entry = target(slug)
    record = json.loads((directory / f"artifact-{slug}.json").read_text(encoding="utf-8"))
    version = source_version()
    if record.get("target") != slug or record.get("revision") != revision or record.get("version") != version:
        raise ValueError("package target, source revision or version mismatch")
    prefix = f"ramsql-{version}-{slug}"
    archive_name = prefix + (".zip" if entry["format"] == "pe" else ".tar.gz")
    expected = {archive_name}
    if entry["kind"] == "native-sb0":
        expected.add(entry["binary"])
    items = record.get("artifacts", [])
    if len(items) != len(expected) or {item["file"] for item in items} != expected:
        raise ValueError("package artifact list differs from expected target files")
    for item in items:
        data = (directory / item["file"]).read_bytes()
        if sha256(data) != item["sha256"] or len(data) != item["bytes"]:
            raise ValueError("package artifact checksum or size mismatch")
    files = archive_contents(directory / archive_name, prefix)
    contents = json.loads(files["contents.json"][0])
    listed = contents.get("files", [])
    if contents.get("schema") != 1 or len(listed) != len(files) - 1 or {item["path"] for item in listed} != set(files) - {"contents.json"}:
        raise ValueError("archive contents manifest must cover every packaged file exactly once")
    for item in listed:
        data, mode = files[item["path"]]
        if sha256(data) != item["sha256"] or len(data) != item["bytes"] or oct(mode) != item["mode"]:
            raise ValueError(f"archive content checksum, size or mode mismatch: {item['path']}")
    provenance = json.loads(files["provenance.json"][0])
    if provenance != record.get("provenance") or provenance.get("version") != version or provenance["source"]["revision"] != revision or provenance.get("target") != entry:
        raise ValueError("archive provenance differs from artifact record or expected source")
    if provenance.get("zpm") != dependency_provenance():
        raise ValueError("archive ZPM dependency differs from checked-out manifest")
    validate_toolchain(provenance.get("toolchain", {}), entry)
    binary = files[entry["binary"]][0]
    validate_binary(binary, entry)
    if entry["kind"] == "native-sb0":
        if (directory / entry["binary"]).read_bytes() != binary:
            raise ValueError("raw native image differs from packaged image")
        report = json.loads(files["runtime-test.json"][0])
        if report.get("passed") is not True or report.get("image_sha256") != sha256(binary):
            raise ValueError("native image lacks matching passed runtime evidence")
    return {"record": record, "files": files, "binary": binary, "archive": archive_name}


def verify(args: argparse.Namespace) -> dict:
    checked = verify_package(args.input, args.target, args.revision)
    entry = target(args.target)
    result = {"schema": 1, "passed": True, "verification": "archive_contents_and_binary_identity",
              "target": args.target, "revision": args.revision, "version": source_version(),
              "archive": checked["archive"],
              "archive_sha256": sha256((args.input / checked["archive"]).read_bytes()),
              "image_sha256": sha256(checked["binary"])}
    if args.install_bin:
        args.install_bin.mkdir(parents=True, exist_ok=True)
        binary_path = args.install_bin / entry["binary"]
        with binary_path.open("xb") as output:
            output.write(checked["binary"])
        binary_path.chmod(0o755)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(json_bytes(result))
    return result


def runtime_report(args: argparse.Namespace) -> dict:
    checked = json.loads(args.verification.read_text(encoding="utf-8"))
    if checked.get("passed") is not True or checked.get("verification") != "archive_contents_and_binary_identity":
        raise ValueError("runtime report requires a successful archive verification")
    if args.tests != HOSTED_RUNTIME_TESTS:
        raise ValueError("runtime report must include the complete hosted integration suite")
    result = dict(checked, verification="hosted_release_artifact_runtime", tests=args.tests,
                  host_os=platform.system(), client_machine=platform.machine(),
                  client_python_version=platform.python_version(), client_pointer_bits=struct.calcsize("P") * 8,
                  runner_os=os.environ.get("RUNNER_OS"), runner_arch=os.environ.get("RUNNER_ARCH"),
                  workflow_run=os.environ.get("GITHUB_RUN_ID"))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(json_bytes(result))
    return result


def assemble(args: argparse.Namespace) -> dict:
    """Fail closed when any target, archive, hash, or source revision is missing."""
    records = []
    expected = {entry["slug"] for entry in TARGETS}
    seen = set()
    filenames = set()
    for path in sorted(args.output.glob("artifact-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        slug = record["target"]
        if slug not in expected or slug in seen:
            raise ValueError(f"unexpected or duplicate target: {slug}")
        seen.add(slug)
        if record["version"] != source_version() or record["revision"] != args.revision:
            raise ValueError(f"source version/revision mismatch: {slug}")
        if target(slug)["kind"] == "native-sb0" and record["provenance"]["verification"]["runtime"] != "passed":
            raise ValueError(f"native SB0 runtime test evidence missing: {slug}")
        for item in record["artifacts"]:
            name = item["file"]
            if Path(name).name != name or name in filenames:
                raise ValueError("invalid or duplicate artifact filename")
            filenames.add(name)
            data = (args.output / name).read_bytes()
            if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
                raise ValueError(f"artifact checksum/size mismatch: {name}")
        checked = verify_package(args.output, slug, args.revision)
        if target(slug)["kind"] == "hosted":
            report = json.loads((args.output / f"runtime-{slug}.json").read_text(encoding="utf-8"))
            entry = target(slug)
            expected_os = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}[entry["runtime"]]
            expected_arch = {"x86_64": "X64", "aarch64": "ARM64"}[entry["arch"]]
            if (report.get("passed") is not True or report.get("verification") != "hosted_release_artifact_runtime"
                    or report.get("target") != slug or report.get("revision") != args.revision
                    or report.get("image_sha256") != sha256(checked["binary"])
                    or report.get("archive_sha256") != sha256((args.output / checked["archive"]).read_bytes())
                    or report.get("tests") != HOSTED_RUNTIME_TESTS
                    or report.get("runner_os") != expected_os or report.get("runner_arch") != expected_arch):
                raise ValueError(f"hosted runtime test evidence missing or mismatched: {slug}")
            record["hosted_runtime_evidence"] = report
        records.append(record)
    if seen != expected:
        raise ValueError(f"release matrix incomplete: missing {sorted(expected - seen)}")
    manifest = {"schema": 1, "project": "ramsql", "version": source_version(),
                "revision": args.revision, "production_ready": False, "targets": records}
    manifest_path = args.output / "release-manifest.json"
    manifest_path.write_bytes(json_bytes(manifest))
    # Include artifact records and evidence as well as binaries. The checksum
    # file excludes itself; no local paths or recursive directory traversal.
    lines = []
    for path in sorted(args.output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256(path.read_bytes())}  {path.name}\n")
    (args.output / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8", newline="\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    single = commands.add_parser("package")
    single.add_argument("--target", required=True)
    single.add_argument("--binary", type=Path, required=True)
    single.add_argument("--output", type=Path, required=True)
    single.add_argument("--revision", required=True)
    single.add_argument("--source-date-epoch", type=int, required=True)
    single.add_argument("--optimize", choices=("ReleaseSafe", "ReleaseSmall", "ReleaseFast"), default="ReleaseSafe")
    single.add_argument("--verification", type=Path)
    single.add_argument("--toolchain-manifest", type=Path,
                        help="exact native source-built Sig provenance; hosted builds use the verified release")
    all_targets = commands.add_parser("assemble")
    all_targets.add_argument("--output", type=Path, required=True)
    all_targets.add_argument("--revision", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--input", type=Path, required=True)
    check.add_argument("--target", required=True)
    check.add_argument("--revision", required=True)
    check.add_argument("--install-bin", type=Path)
    check.add_argument("--report", type=Path)
    runtime = commands.add_parser("runtime-report")
    runtime.add_argument("--verification", type=Path, required=True)
    runtime.add_argument("--report", type=Path, required=True)
    runtime.add_argument("--tests", nargs="+", required=True)
    args = parser.parse_args()
    result = {"package": package, "assemble": assemble, "verify": verify,
              "runtime-report": runtime_report}[args.command](args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
