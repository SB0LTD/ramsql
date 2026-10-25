"""Copy the reference image's zoneinfo for RAMSQL's --timezone-dir.

Both engines must use the same tzdata, so the differential harnesses pass
RAMSQL the zone files of the pinned PostgreSQL reference image:

    python tools/fetch_tzdata.py --image postgres:18-alpine

copies /usr/share/zoneinfo out of the locally available image (no automatic
pull) into .sig-cache/tzdata/<image-id>/ and writes ramsql-zones.idx there:
one canonical relative path per TZif file (hard and symbolic links
included), which RAMSQL streams to resolve case-insensitive zone names
(design §4.8). An existing complete copy is reused. Prints the directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = "ramsql-zones.idx"


def docker(*arguments: str) -> str:
    completed = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError("docker " + " ".join(arguments[:2]) + " failed: " + completed.stderr.strip())
    return completed.stdout.strip()


def image_id(image: str) -> str:
    info = json.loads(docker("image", "inspect", image))[0]
    return info["Id"]


def tzdata_directory(image: str, root: Path = ROOT) -> Path:
    """The cache directory for `image` (it may not exist yet)."""
    return root / ".sig-cache" / "tzdata" / image_id(image).replace("sha256:", "")


def write_index(directory: Path) -> int:
    names = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == INDEX:
            continue
        with path.open("rb") as handle:
            if handle.read(5)[:4] != b"TZif":
                continue
        relative = path.relative_to(directory).as_posix()
        if len(relative) <= 64:
            names.append(relative)
    (directory / INDEX).write_text("".join(name + "\n" for name in names), encoding="ascii", newline="\n")
    return len(names)


def fetch(image: str, root: Path = ROOT) -> Path:
    target = tzdata_directory(image, root)
    if (target / INDEX).is_file() and (target / "UTC").is_file():
        return target
    container = docker("create", image_id(image))
    try:
        with tempfile.TemporaryDirectory(dir=root / ".sig-cache") as scratch:
            # -L dereferences symbolic links so every name is a regular file.
            docker("cp", "-L", container + ":/usr/share/zoneinfo", scratch)
            copied = Path(scratch) / "zoneinfo"
            if not (copied / "UTC").is_file():
                raise RuntimeError("the image has no /usr/share/zoneinfo/UTC")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            if partial.exists():
                shutil.rmtree(partial)
            shutil.copytree(copied, partial)
            count = write_index(partial)
            if count == 0:
                raise RuntimeError("no TZif files found in the image zoneinfo")
            if target.exists():
                shutil.rmtree(target)
            partial.rename(target)
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, text=True, timeout=60)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available reference image")
    args = parser.parse_args()
    try:
        directory = fetch(args.image)
    except Exception as error:  # noqa: BLE001 - reported to the caller
        print("fetch_tzdata: " + str(error), file=sys.stderr)
        return 1
    count = sum(1 for _ in (directory / INDEX).read_text(encoding="ascii").splitlines())
    print(f"{directory} ({count} zones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
