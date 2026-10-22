"""Executable contracts for target identity and deterministic release packaging."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import package_release as package
import release_matrix as matrix


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
                    if entry["kind"] == "hosted":
                        fixture_env = {"RUNNER_OS": {"linux": "Linux", "windows": "Windows", "macos": "macOS"}[entry["runtime"]],
                                       "RUNNER_ARCH": {"x86_64": "X64", "aarch64": "ARM64"}[entry["arch"]]}
                        with patch.dict(package.os.environ, fixture_env):
                            package.runtime_report(argparse.Namespace(verification=verified,
                                report=output / f"runtime-{entry['slug']}.json",
                                tests=package.HOSTED_RUNTIME_TESTS))
            result = package.assemble(args)
            self.assertEqual(len(result["targets"]), 8)
            self.assertFalse(result["production_ready"])
            checksums = (first / "SHA256SUMS.txt").read_text()
            self.assertIn("release-manifest.json", checksums)
            self.assertNotIn("SHA256SUMS.txt", checksums)
            archive = next(first.glob("*.zip"))
            archive.write_bytes(archive.read_bytes() + b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum"):
                package.assemble(args)

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
