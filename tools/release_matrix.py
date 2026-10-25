"""Canonical release targets, aligned with SLS's hosted and native SB0 matrix.

This is build metadata, not a declaration of production PostgreSQL compatibility.
The SB0S and SB0K entries are distinct native image formats for the consolidated
aarch64-sb0 ABI. They must never be substituted with Linux executables.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SIG_TAG = "sig-0.6.4-zig0.17.0.90a898d"
SIG_ASSET = SIG_TAG + "-x86_64-linux.tar.xz"
SIG_SHA256 = "8dd32622531b5f81dbb12502c78e187141dd42ea3553f4d9e2c09c3e82d64a89"
COMPILER_HOSTS = {
    "Linux/X64": ("x86_64-linux.tar.xz", SIG_SHA256),
    "Linux/ARM64": ("aarch64-linux.tar.xz", "5b1d4e2e748a362f1379a3f5a597b9bb9d401505bcb7b506503fa73226d1d001"),
    "macOS/ARM64": ("aarch64-macos.tar.xz", "75ae868e63b6a90ffd3e282f410b18e5098ea87d02cbdc0fd95224f5e922491f"),
    "Windows/X64": ("x86_64-windows.zip", "49fc99ac20af7841a5b686c68a8720515deda9c28dcad0cb9191fa0de30e33a0"),
}
# Full source pin for the native compiler candidate. Database and loader QEMU
# probes must pass before it can produce a release bundle; never use a branch.
NATIVE_SIG_REVISION = "90a898dcbe088455f07449b7de1feaea763c42a0"

# SLS .github/workflows/release.yaml publishes these six hosted architectures
# plus an aarch64-sb0 SB0X app. RAMSQL also packages the requested SB0K image.
TARGETS = [
    {"slug": "x86_64-linux", "triple": "x86_64-linux-gnu", "format": "elf", "arch": "x86_64", "kind": "hosted", "binary": "ramsql", "runtime": "linux"},
    {"slug": "aarch64-linux", "triple": "aarch64-linux-gnu", "format": "elf", "arch": "aarch64", "kind": "hosted", "binary": "ramsql", "runtime": "linux"},
    {"slug": "x86_64-macos", "triple": "x86_64-macos", "format": "macho", "arch": "x86_64", "kind": "hosted", "binary": "ramsql", "runtime": "macos"},
    {"slug": "aarch64-macos", "triple": "aarch64-macos", "format": "macho", "arch": "aarch64", "kind": "hosted", "binary": "ramsql", "runtime": "macos"},
    {"slug": "x86_64-windows", "triple": "x86_64-windows", "format": "pe", "arch": "x86_64", "kind": "hosted", "binary": "ramsql.exe", "runtime": "windows"},
    {"slug": "aarch64-windows", "triple": "aarch64-windows", "format": "pe", "arch": "aarch64", "kind": "hosted", "binary": "ramsql.exe", "runtime": "windows"},
    {"slug": "aarch64-sb0-sb0x", "triple": "aarch64-sb0", "format": "sb0x", "arch": "aarch64", "kind": "native-sb0", "binary": "ramsql.sb0x", "runtime": "sb0", "execution_context": "EL0"},
    {"slug": "aarch64-sb0-sb0k", "triple": "aarch64-sb0", "format": "sb0k", "arch": "aarch64", "kind": "native-sb0", "binary": "ramsql.sb0k", "runtime": "sb0", "execution_context": "EL1"},
]

HOSTED_RUNNERS = {
    "x86_64-linux": "ubuntu-latest", "aarch64-linux": "ubuntu-24.04-arm",
    "x86_64-macos": "macos-15-intel", "aarch64-macos": "macos-latest",
    "x86_64-windows": "windows-latest", "aarch64-windows": "windows-11-arm",
}


def target(slug: str) -> dict:
    for entry in TARGETS:
        if entry["slug"] == slug:
            return dict(entry)
    raise ValueError(f"unknown release target: {slug}")


def compiler_host(host: str) -> dict:
    suffix, digest = COMPILER_HOSTS[host]
    return {"tag": SIG_TAG, "asset": SIG_TAG + "-" + suffix, "sha256": digest}


def source_version(root: Path = ROOT) -> str:
    manifest = re.search(r'\.version\s*=\s*"([^"]+)"', (root / "build.sig.zon").read_text(encoding="utf-8"))
    facade = re.search(r'pub const version\s*=\s*"([^"]+)"', (root / "src/root.sig").read_text(encoding="utf-8"))
    if not manifest or not facade or manifest[1] != facade[1]:
        raise ValueError("build.sig.zon and src/root.sig versions must agree")
    version = manifest[1]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", version):
        raise ValueError("release version must be a path-safe semantic version")
    return version


def metadata(tag: str | None = None) -> dict:
    version = source_version()
    expected = f"v{version}"
    if tag and tag != expected:
        raise ValueError(f"release tag {tag!r} differs from source version {expected!r}")
    return {"version": version, "tag": expected, "prerelease": "-" in version,
            "matrix": {"include": TARGETS},
            "hosted_matrix": {"include": [dict(entry, runner=HOSTED_RUNNERS[entry["slug"]],
                                          python_arch="x64" if entry["runtime"] == "windows" or entry["arch"] == "x86_64" else "arm64")
                                          for entry in TARGETS if entry["kind"] == "hosted"]},
            "native_matrix": {"include": [entry for entry in TARGETS if entry["kind"] == "native-sb0"]},
            "native_sig_revision": NATIVE_SIG_REVISION,
            "sig_tag": SIG_TAG,
            "sig_asset": SIG_ASSET, "sig_sha256": SIG_SHA256}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--compiler-host", choices=COMPILER_HOSTS)
    args = parser.parse_args()
    if args.compiler_host:
        print(json.dumps(compiler_host(args.compiler_host)))
        return
    data = metadata(args.tag)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
            for key, value in data.items():
                encoded = json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, bool)) else value
                output.write(f"{key}={encoded}\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
