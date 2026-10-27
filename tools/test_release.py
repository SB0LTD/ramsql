"""Executable contracts for target identity and deterministic release packaging."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import package_release as package
import release_matrix as matrix
from test_qualify_postgres import synthetic_bundle


def synthetic_binary(entry: dict) -> bytes:
    # Test fixtures exercise header validation, never masquerade as real builds.
    data = bytearray(256)
    if entry["format"] == "elf":
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 16, 2)
        struct.pack_into("<H", data, 18, {"x86_64": 62, "aarch64": 183}[entry["arch"]])
        struct.pack_into("<I", data, 20, 1)
    elif entry["format"] == "macho":
        data[:4] = b"\xcf\xfa\xed\xfe"
        struct.pack_into("<I", data, 4, {"x86_64": 0x01000007, "aarch64": 0x0100000C}[entry["arch"]])
        struct.pack_into("<I", data, 12, 2)
    elif entry["format"] == "pe":
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, {"x86_64": 0x8664, "aarch64": 0xAA64}[entry["arch"]])
        struct.pack_into("<HHH", data, 84, 112, 2, 0x20B)
    else:
        data[:4] = entry["format"].upper().encode()
    return bytes(data)


def synthetic_benchmark(revision, image_sha256):
    # Identity/gate fixture only: no fabricated performance measurements.
    return {"measurement_checks_passed": True,
            "environment": {"git_head": revision, "ramsql_sha256": image_sha256},
            "reference": {"deployment": "owned native process"},
            "durability": {"postgres": {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on"},
                           "ramsql_reopen_row_check": "passed after abrupt process termination"},
            "workload": {"trials": 3, "concurrent_clients": [1, 4], "operations_per_trial": 256},
            "measurements": [{"engine": engine, "workload": case, "clients": clients, "trial": trial}
                             for engine in ("postgres", "ramsql")
                             for case in ("schema_create_drop", "insert", "point_update", "point_select", "scan_limit_16")
                             for clients in (1, 4) for trial in (1, 2, 3)]}


class ReleaseContracts(unittest.TestCase):
    def test_matrix_has_six_hosted_and_two_distinct_sb0_formats(self):
        self.assertEqual(len(matrix.TARGETS), 8)
        self.assertEqual(len({item["slug"] for item in matrix.TARGETS}), 8)
        native = [item for item in matrix.TARGETS if item["kind"] == "native-sb0"]
        self.assertEqual({item["triple"] for item in native}, {"aarch64-sb0"})
        self.assertEqual({item["format"] for item in native}, {"sb0x", "sb0k"})

    def test_tag_matches_source_version(self):
        self.assertEqual(matrix.metadata()["tag"], "v" + matrix.source_version())
        with self.assertRaises(ValueError):
            matrix.metadata("v999.0.0")

    def test_linux_builds_use_matched_native_no_libc_toolchains(self):
        entries=matrix.metadata()['hosted_matrix']['include']
        for entry in entries:
            if entry['runtime']=='linux':
                self.assertEqual(entry['build_runner'],entry['runner'])
                host='Linux/ARM64' if entry['arch']=='aarch64' else 'Linux/X64'
                self.assertEqual(entry['build_compiler'],matrix.compiler_host(host))
                provenance=package.published_toolchain(entry)
                self.assertEqual(provenance['asset'],entry['build_compiler']['asset'])
                package.validate_toolchain(provenance,entry)
            else:
                self.assertEqual(entry['build_runner'],'ubuntu-latest')

    def test_public_sig_runtime_is_an_independent_immutable_source_pin(self):
        runtime=package.runtime_provenance()
        self.assertRegex(runtime['archive'],r'^https://github\.com/SB0LTD/sig/archive/[0-9a-f]{40}\.tar\.gz$')
        self.assertRegex(runtime['sha256'],r'^[0-9a-f]{64}$')
        self.assertNotEqual(runtime,package.dependency_provenance())

    def test_every_target_rejects_other_formats_and_architectures(self):
        for expected in matrix.TARGETS:
            for other in matrix.TARGETS:
                with self.subTest(expected=expected["slug"], actual=other["slug"]):
                    if expected["format"] == other["format"] and expected["arch"] == other["arch"]:
                        package.validate_binary(synthetic_binary(other), expected)
                    else:
                        with self.assertRaises(ValueError):
                            package.validate_binary(synthetic_binary(other), expected)

    def test_truncated_headers_rejected(self):
        for entry in matrix.TARGETS:
            for length in (0, 1, 4, 16):
                with self.subTest(target=entry["slug"], length=length):
                    with self.assertRaises(ValueError):
                        package.validate_binary(synthetic_binary(entry)[:length], entry)

    def test_object_files_do_not_pass_as_executables(self):
        for entry in matrix.TARGETS:
            data = bytearray(synthetic_binary(entry))
            if entry["format"] == "elf":
                struct.pack_into("<H", data, 16, 1)
            elif entry["format"] == "macho":
                struct.pack_into("<I", data, 12, 1)
            elif entry["format"] == "pe":
                struct.pack_into("<H", data, 86, 0)
            else:
                continue
            with self.subTest(target=entry["slug"]):
                with self.assertRaises(ValueError):
                    package.validate_binary(data, entry)

    def test_archives_reproducible_and_all_targets_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first, second = base / "first", base / "second"
            revision = "a" * 40
            args = argparse.Namespace(output=first, revision=revision)
            with self.assertRaises(ValueError):
                package.assemble(args)
            for entry in matrix.TARGETS:
                fixture = base / (entry["slug"] + ".fixture")
                fixture.write_bytes(synthetic_binary(entry))
                verification = None
                if entry["kind"] == "native-sb0":
                    verification = base / (entry["slug"] + ".test-fixture.json")
                    verification.write_text(json.dumps({"passed": True,
                        "image_sha256": package.sha256(fixture.read_bytes()),
                        "note": "unit-test fixture only"}))
                records = []
                for output in (first, second):
                    records.append(package.package(argparse.Namespace(
                        target=entry["slug"], binary=fixture, output=output,
                        revision=revision, source_date_epoch=1234567890,
                        optimize="ReleaseSafe", verification=verification)))
                self.assertEqual(records[0], records[1])
                for artifact in records[0]["artifacts"]:
                    self.assertEqual((first / artifact["file"]).read_bytes(),
                                     (second / artifact["file"]).read_bytes())
                for output in (first, second):
                    verified = output / (entry["slug"] + ".verified.json")
                    package.verify(argparse.Namespace(input=output, target=entry["slug"],
                        revision=revision, install_bin=None, report=verified))
                    checked = package.verify_package(output, entry['slug'], revision)
                    for name in ('README.md', 'CHANGELOG.md', 'ROADMAP.md', 'CAPACITY.md',
                                 'OPERATIONS.md', 'SQL_REFERENCE.md', 'BETA_USABILITY.md', 'create_verifier.py'):
                        self.assertIn(name, checked['files'])
                    readme = checked['files']['README.md'][0].decode('utf-8')
                    self.assertIn('](BETA_USABILITY.md)', readme)
                    self.assertIn('](RELEASE.md)', readme)
                    self.assertIn('/blob/' + revision + '/benchmarks/', readme)
                    if entry["kind"] == "hosted":
                        fixture_env = {"RUNNER_OS": {"linux": "Linux", "windows": "Windows", "macos": "macOS"}[entry["runtime"]],
                                       "RUNNER_ARCH": {"x86_64": "X64", "aarch64": "ARM64"}[entry["arch"]]}
                        with patch.dict(package.os.environ, fixture_env):
                            package.runtime_report(argparse.Namespace(verification=verified,
                                report=output / f"runtime-{entry['slug']}.json",
                                tests=package.HOSTED_RUNTIME_TESTS))
            evidence = synthetic_benchmark(revision, package.sha256(synthetic_binary(matrix.target("x86_64-linux"))))
            (first / "benchmark-postgres.json").write_text(json.dumps(evidence))
            qualification = synthetic_bundle(revision, package.sha256(synthetic_binary(matrix.target("x86_64-linux"))))
            (first / "postgres-qualification.json").write_text(json.dumps(qualification))
            result = package.assemble(args)
            self.assertEqual(len(result["targets"]), 8)
            self.assertFalse(result["production_ready"])
            self.assertEqual(result["postgres_qualification_evidence"], "postgres-qualification.json")
            checksums = (first / "SHA256SUMS.txt").read_text()
            self.assertIn("release-manifest.json", checksums)
            self.assertNotIn("SHA256SUMS.txt", checksums)
            archive = next(first.glob("*.zip"))
            archive.write_bytes(archive.read_bytes() + b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum"):
                package.assemble(args)

    def test_benchmark_requires_exact_image_durability_and_complete_trials(self):
        revision, digest = "a" * 40, "b" * 64
        evidence = synthetic_benchmark(revision, digest)
        package.validate_benchmark(evidence, revision, digest)
        for section, key, invalid in (("environment", "git_head", "c" * 40),
                                      ("environment", "ramsql_sha256", "d" * 64),
                                      ("reference", "deployment", "Docker"),
                                      ("durability", "postgres", {"fsync": "off"}),
                                      ("durability", "ramsql_reopen_row_check", "failed"),
                                      ("workload", "trials", 1)):
            bad = copy.deepcopy(evidence)
            bad[section][key] = invalid
            with self.assertRaises(ValueError):
                package.validate_benchmark(bad, revision, digest)
        bad = copy.deepcopy(evidence)
        bad["measurements"].pop()
        with self.assertRaises(ValueError):
            package.validate_benchmark(bad, revision, digest)
        bad["measurements"].append(bad["measurements"][0])
        with self.assertRaises(ValueError):
            package.validate_benchmark(bad, revision, digest)

    def test_archive_paths_and_links_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "traversal.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                entry = tarfile.TarInfo("package/../escape")
                archive.addfile(entry)
            with self.assertRaisesRegex(ValueError, "file path"):
                package.archive_contents(path, "package")
            symlink = Path(temporary) / "link.tar.gz"
            with tarfile.open(symlink, "w:gz") as archive:
                entry = tarfile.TarInfo("package/link")
                entry.type = tarfile.SYMTYPE
                entry.linkname = "outside"
                archive.addfile(entry)
            with self.assertRaisesRegex(ValueError, "regular files"):
                package.archive_contents(symlink, "package")

    def test_runtime_evidence_must_match_exact_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            entry = matrix.target("aarch64-sb0-sb0k")
            binary = base / "image.sb0k"
            binary.write_bytes(synthetic_binary(entry))
            report = base / "report.json"
            report.write_text(json.dumps({"passed": True, "image_sha256": "0" * 64}))
            args = argparse.Namespace(target=entry["slug"], binary=binary,
                output=base / "dist", revision="a" * 40, source_date_epoch=1234567890,
                optimize="ReleaseSafe", verification=report)
            with self.assertRaisesRegex(ValueError, "exact image"):
                package.package(args)
            self.assertFalse(args.output.exists())

    def test_pinned_zpm_provenance(self):
        provenance = package.dependency_provenance()
        self.assertIn("https://github.com/SB0LTD/zpm/archive/", provenance["archive"])
        self.assertEqual(len(provenance["sha256"]), 64)

    def test_native_source_compiler_requires_exact_provenance(self):
        native = matrix.target("aarch64-sb0-sb0x")
        provenance = {"schema": 1, "kind": "source-build",
                      "source_repository": "https://github.com/SB0LTD/sig",
                      "source_revision": matrix.NATIVE_SIG_REVISION, "library_revision": matrix.NATIVE_SIG_REVISION,
                      "compiler_sha256": "b" * 64, "compiler_version": "unit-test fixture",
                      "bootstrap": package.published_toolchain()}
        package.validate_toolchain(provenance, native)
        with self.assertRaisesRegex(ValueError, "only native"):
            package.validate_toolchain(provenance, matrix.target("x86_64-linux"))
        provenance["library_revision"] = "c" * 40
        with self.assertRaisesRegex(ValueError, "exact source"):
            package.validate_toolchain(provenance, native)
        provenance["library_revision"] = "f" * 40
        provenance["source_revision"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "pinned native"):
            package.validate_toolchain(provenance, native)


if __name__ == "__main__":
    unittest.main(verbosity=2)
