#!/usr/bin/env python3
"""Boot a native RAMSQL image and verify PostgreSQL packets and SQL under QEMU.

SB0X runs at EL0 under the public single-process ABI loader fixture in this
repository. This executes the real SB0X segments and SVC channel interface;
it does not claim integration with a complete SB0S distribution. Both profiles
use trusted, memory-only transports, without native TCP, storage, or SCRAM.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
LOADER_LINKER = """ENTRY(_start)
SECTIONS {
 . = 0x40200000; __sb0_image_start = .;
 .sb0k : {
  LONG(0x4b304253) SHORT(1) SHORT(64) SHORT(1) SHORT(0) LONG(1)
  QUAD(_start - __sb0_image_start) QUAD(__sb0_file_end - __sb0_image_start)
  QUAD(0) LONG(0) LONG(0) QUAD(0) QUAD(0x40200000)
 }
 .text : ALIGN(2048) { KEEP(*(.text._start .text.*._start .text.*_start)) *(.text .text.*) }
 .rodata : ALIGN(16) { *(.rodata .rodata.*) }
 .data : ALIGN(16) { *(.data .data.*) }
 __sb0_file_end = .;
 .bss (NOLOAD) : ALIGN(16) { *(.bss .bss.* COMMON) }
 /DISCARD/ : { *(.comment .note .note.* .eh_frame .eh_frame_hdr) }
}
"""


def build_loader(output, compiler=None, library=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    script = output.with_suffix(".ld")
    script.write_text(LOADER_LINKER, encoding="utf-8")
    command = [compiler or os.environ.get("SIG", "sig"), "build-exe",
               str(ROOT / "tools/test_sb0_loader.sig"), "-target", "aarch64-sb0",
               "-OReleaseSmall", "-fno-stack-check", "-fno-stack-protector", "-fno-unwind-tables",
               "-fno-compiler-rt", "-fstrip", "--script", str(script), "-femit-bin=" + str(output)]
    library = library or os.environ.get("SIG_LIB_DIR")
    if library:
        command += ["--Sig-lib-dir", str(library)]
    subprocess.run(command, cwd=ROOT, check=True)
    return output


class Proof:
    def __init__(self):
        self.assertions = 0
        self.tests = []

    def check(self, condition, reason):
        self.assertions += 1
        if not condition:
            raise AssertionError(reason)


def packet(tag, body=b""):
    return tag + struct.pack("!I", 4 + len(body)) + body


class Guest:
    def __init__(self, command, proof):
        self.proof = proof
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0)
        self.queue = queue.Queue()
        self.buffer = bytearray()
        self.transcript = bytearray()
        self.errors = bytearray()
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.reader.start()
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.error_reader.start()

    def read_output(self):
        while True:
            data = self.process.stdout.read(4096)
            if not data:
                self.queue.put(None)
                return
            self.queue.put(data)

    def read_errors(self):
        self.errors.extend(self.process.stderr.read())

    def more(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("QEMU response timed out: " + repr(bytes(self.transcript[-1000:])))
        try:
            data = self.queue.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError("QEMU response timed out: " + repr(bytes(self.transcript[-1000:])))
        if data is None:
            raise RuntimeError("QEMU exited before protocol completion: " + repr(bytes(self.errors)))
        self.buffer.extend(data)
        self.transcript.extend(data)

    def until(self, marker, seconds=45):
        deadline = time.monotonic() + seconds
        while marker not in self.buffer:
            self.more(deadline)
        end = self.buffer.index(marker) + len(marker)
        result = bytes(self.buffer[:end])
        del self.buffer[:end]
        return result

    def exact(self, length, seconds=10):
        deadline = time.monotonic() + seconds
        while len(self.buffer) < length:
            self.more(deadline)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def send(self, data):
        # Drain the real 16-byte PL011 FIFO between small writes. All frames
        # are consequently fragmented at arbitrary header/body boundaries.
        for offset in range(0, len(data), 8):
            self.process.stdin.write(data[offset:offset + 8])
            self.process.stdin.flush()
            time.sleep(0.01)

    def response(self):
        frames = []
        for _ in range(4096):
            header = self.exact(5)
            length = struct.unpack("!I", header[1:])[0]
            if not 4 <= length <= 2 * 1024 * 1024 and header.startswith(b"SB0-"):
                self.until(b"\n", seconds=2)  # collect complete kernel fault diagnostic
            self.proof.check(4 <= length <= 2 * 1024 * 1024,
                             f"invalid native PostgreSQL frame length: {header!r}; transcript tail={bytes(self.transcript[-1000:])!r}")
            body = self.exact(length - 4)
            frames.append((header[:1], body))
            if header[:1] == b"Z":
                self.proof.check(len(body) == 1 and body in (b"I", b"T", b"E"), "invalid transaction state")
                return frames
        raise AssertionError("response never reached ReadyForQuery")

    def query(self, sql, expected_rows=None, status=b"I", error=False):
        self.send(packet(b"Q", sql.encode("utf-8") + b"\0"))
        frames = self.response()
        errors = [body for tag, body in frames if tag == b"E"]
        self.proof.check(bool(errors) == error, f"unexpected SQL result for {sql}: {errors!r}")
        self.proof.check(frames[-1] == (b"Z", status), f"unexpected transaction status for {sql}")
        rows = []
        for tag, body in frames:
            if tag != b"D":
                continue
            self.proof.check(len(body) >= 2, "truncated DataRow")
            count = struct.unpack_from("!H", body)[0]
            at = 2
            row = []
            for _ in range(count):
                self.proof.check(at + 4 <= len(body), "truncated DataRow length")
                length = struct.unpack_from("!i", body, at)[0]
                at += 4
                self.proof.check(length >= -1 and (length == -1 or at + length <= len(body)), "invalid DataRow value")
                row.append(None if length == -1 else body[at:at + length].decode("utf-8"))
                if length >= 0:
                    at += length
            self.proof.check(at == len(body), "trailing DataRow bytes")
            rows.append(row)
        if expected_rows is not None:
            self.proof.check(rows == expected_rows, f"wrong native SQL rows for {sql}: {rows!r}")
        return frames

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process.stdin.close()
        self.reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()


def validate_image(image, kind, proof):
    proof.check(len(image) >= 64 and image[:4] == kind.upper().encode(), "not a native image of the requested format")
    if kind == "sb0k":
        version, header, boot_abi = struct.unpack_from("<HHH", image, 4)
        entry, size = struct.unpack_from("<QQ", image, 16)
        proof.check((version, header, boot_abi) == (1, 64, 1), "invalid SB0K version/header/boot ABI")
        proof.check(64 <= entry < len(image) and size == len(image), "invalid native kernel entry/image length")
        return 0x40200000 + entry
    proof.check(image[4] == 1 and image[5] == 0 and struct.unpack_from("<H", image, 6)[0] <= 1, "unsupported SB0X ABI")
    entry = struct.unpack_from("<Q", image, 8)[0]
    count = struct.unpack_from("<H", image, 16)[0]
    memory_size = struct.unpack_from("<Q", image, 32)[0]
    proof.check(1 <= count <= 8 and 64 + count * 40 <= len(image), "invalid SB0X segment table")
    executable = False
    writable = False
    ranges = []
    for index in range(count):
        file_at, virtual, file_size, mem_size, flags, reserved = struct.unpack_from("<QQQQII", image, 64 + index * 40)
        proof.check(reserved == 0 and flags & ~7 == 0 and file_size <= mem_size and virtual + mem_size <= memory_size,
                    "invalid SB0X segment bounds/flags")
        proof.check(file_size == 0 or (file_at >= 64 + count * 40 and file_at + file_size <= len(image)), "invalid SB0X payload bounds")
        proof.check(not any(virtual < end and start < virtual + mem_size for start, end in ranges), "overlapping native segments")
        ranges.append((virtual, virtual + mem_size))
        executable |= bool(flags & 4 and virtual <= entry < virtual + mem_size)
        writable |= bool(flags & 2 and mem_size > 0)
    proof.check(executable and writable, "native application needs executable entry and writable state")
    return None


def loader_command(qemu, loader, loader_entry, image, metadata):
    return [qemu, "-machine", "virt", "-cpu", "cortex-a57", "-m", "512M", "-smp", "1",
            "-no-reboot", "-display", "none", "-monitor", "none", "-serial", "stdio",
            "-device", f"loader,file={Path(loader).resolve()},addr=0x40200000,force-raw=on",
            "-device", f"loader,file={Path(image).resolve()},addr=0x48000000,force-raw=on",
            "-device", f"loader,file={Path(metadata).resolve()},addr=0x47e00000,force-raw=on",
            "-device", f"loader,addr={loader_entry:#x},cpu-num=0"]


def reject_invalid_images(qemu, loader, loader_entry, original, directory, metadata, proof):
    mutations = []
    invalid = bytearray(original)
    invalid[0] = 0
    mutations.append(("bad_magic", invalid))
    invalid = bytearray(original)
    struct.pack_into("<Q", invalid, 8, struct.unpack_from("<Q", original, 32)[0] + 4096)
    mutations.append(("entry_outside_image", invalid))
    invalid = bytearray(original)
    struct.pack_into("<Q", invalid, 64 + 16, len(original) + 1)
    mutations.append(("segment_file_out_of_bounds", invalid))
    for name, image in mutations:
        path = Path(directory) / f"invalid-{name}.sb0x"
        path.write_bytes(image)
        guest = Guest(loader_command(qemu, loader, loader_entry, path, metadata), proof)
        try:
            guest.until(b"SB0-ABI-LOADER-REJECT", seconds=15)
            proof.check(b"SB0-ABI-LOADER-SVC-EL0" not in guest.transcript,
                        f"malformed image {name} reached userspace")
            proof.tests.append("runtime_loader_rejects_" + name)
        finally:
            guest.close()


def exercise(guest, kind, proof):
    ready = f"RAMSQL-{kind.upper()}-READY memory-only postgres {'UART' if kind == 'sb0k' else 'channel'}".encode()
    boot = guest.until(ready)
    guest.until(b"\n")  # consume only the startup marker's line terminator
    if kind == "sb0x":
        proof.check(b"SB0-ABI-LOADER-MAPPED" in boot and b"SB0-ABI-LOADER-SVC-EL0" in boot,
                    "public ABI fixture did not map the native application and receive an EL0 SVC")
        proof.tests.append("public_ABI_loader_MMU_RX_RW_BSS_stack_BHB_EL0_SVC")
    else:
        proof.tests.append("privileged_native_reset_and_UART")
    body = struct.pack("!I", 196608) + b"user\0ramsql\0database\0ramsql\0client_encoding\0UTF8\0\0"
    guest.send(struct.pack("!I", len(body) + 4) + body)
    startup = guest.response()
    proof.check((b"R", b"\0\0\0\0") in startup and not any(tag == b"E" for tag, _ in startup), "native PostgreSQL startup failed")
    proof.tests.append("fragmented_postgres_startup")
    guest.query("CREATE TABLE sb0_probe (id INTEGER PRIMARY KEY, value TEXT)")
    guest.query("INSERT INTO sb0_probe VALUES (7, 'native PostgreSQL')")
    guest.query("INSERT INTO sb0_probe VALUES (8, NULL)")
    guest.query("SELECT id,value FROM sb0_probe ORDER BY id", [["7", "native PostgreSQL"], ["8", None]])
    proof.tests.append("SQL_create_insert_select_null")
    # Shuffled dense keys force ordered hash probes rather than the physical
    # order proof. Exercise signed 128-bit interval intermediates on native
    # code generation, including endpoints just outside the i64 domain.
    guest.query("CREATE TABLE range_probe (id BIGINT PRIMARY KEY)")
    guest.query("INSERT INTO range_probe VALUES " + ",".join(f"({(i*17)%64})" for i in range(64)))
    guest.query("SELECT id FROM range_probe WHERE id>=7 AND id<19 ORDER BY id LIMIT 3", [["7"],["8"],["9"]])
    guest.query("SELECT id FROM range_probe WHERE 19>id AND 7<=id ORDER BY id DESC LIMIT 3", [["18"],["17"],["16"]])
    guest.query("SELECT id FROM range_probe WHERE id>7 AND id<=11 ORDER BY id LIMIT 3", [["8"],["9"],["10"]])
    guest.query("SELECT id FROM range_probe WHERE id < -9223372036854775808 ORDER BY id LIMIT 3", [])
    guest.query("SELECT id FROM range_probe WHERE id > 9223372036854775807 ORDER BY id LIMIT 3", [])
    guest.query("SELECT COUNT(*) FROM range_probe", [["64"]])
    guest.query("SELECT id,SUM(id) OVER (ORDER BY id ROWS 1 PRECEDING),MIN(id) OVER (ORDER BY id ROWS 1 PRECEDING EXCLUDE CURRENT ROW) FROM range_probe ORDER BY id LIMIT 3",
                [["0", "0", None], ["1", "1", "0"], ["2", "3", "1"]])
    guest.query("SELECT id,COUNT(*) FILTER (WHERE id>0) OVER (ORDER BY id GROUPS 1 PRECEDING EXCLUDE TIES),MAX(id) OVER (ORDER BY id GROUPS 1 PRECEDING) FROM range_probe ORDER BY id LIMIT 3",
                [["0", "0", "0"], ["1", "1", "1"], ["2", "2", "2"]])
    guest.query("SELECT id,COUNT(*) OVER (ORDER BY id ROWS BETWEEN 9223372036854775807 PRECEDING AND 9223372036854775807 FOLLOWING),COUNT(*) OVER (ORDER BY id RANGE BETWEEN 9223372036854775807 PRECEDING AND 9223372036854775807 FOLLOWING) FROM range_probe ORDER BY id LIMIT 3",
                [["0", "64", "64"], ["1", "64", "64"], ["2", "64", "64"]])
    proof.tests.append("bounded_window_prefix_tree_FILTER_GROUPS_RANGE_exclusion_extreme_offsets")
    guest.query("DROP TABLE range_probe")
    proof.tests.append("dense_ordered_PK_ranges_and_i64_endpoints")
    guest.query("BEGIN", status=b"T")
    guest.query("UPDATE sb0_probe SET value='transaction view' WHERE id=7", status=b"T")
    guest.query("SELECT value FROM sb0_probe WHERE id=7", [["transaction view"]], status=b"T")
    guest.query("ROLLBACK")
    guest.query("SELECT value FROM sb0_probe WHERE id=7", [["native PostgreSQL"]])
    proof.tests.append("independent_transaction_rollback")
    guest.query("BEGIN", status=b"T")
    guest.query("UPDATE sb0_probe SET value='committed' WHERE id=7", status=b"T")
    guest.query("COMMIT")
    guest.query("SELECT value FROM sb0_probe WHERE id=7", [["committed"]])
    proof.tests.append("transaction_commit")
    # Exercise shared prepared-statement inference and binary integer encoding,
    # including Describe's bounded result storage on the native stack.
    parse = packet(b"P", b"\0SELECT id,value FROM sb0_probe WHERE id=$1\0" + struct.pack("!HI", 1, 20))
    bind = packet(b"B", b"\0\0" + struct.pack("!HHHIqHH", 1, 1, 1, 8, 7, 1, 1))
    describe = packet(b"D", b"P\0")
    execute = packet(b"E", b"\0" + struct.pack("!I", 0))
    guest.send(parse + bind + describe + execute + packet(b"S"))
    extended = guest.response()
    proof.check(not any(tag == b"E" for tag, _ in extended), f"native extended query failed: {extended!r}")
    proof.check(any(tag == b"1" for tag, _ in extended) and any(tag == b"2" for tag, _ in extended), "missing native Parse/Bind completion")
    expected_binary = struct.pack("!HIiI", 2, 4, 7, len(b"committed")) + b"committed"
    proof.check([body for tag, body in extended if tag == b"D"] == [expected_binary], "wrong native binary integer/text result")
    proof.tests.append("extended_Parse_Bind_Describe_Execute_binary")
    large_text = "native ' payload " + "x" * 100
    escaped = large_text.replace("'", "''")
    for number in range(20, 52):
        guest.query(f"INSERT INTO sb0_probe VALUES ({number}, '{escaped}')")
    guest.query("SELECT id,value FROM sb0_probe WHERE id>=20 ORDER BY id",
                [[str(number), large_text] for number in range(20, 52)])
    proof.tests.append("large_text_aggregate_copies_and_multirow_result")
    guest.query("DELETE FROM sb0_probe WHERE id>=20")
    guest.query("BEGIN", status=b"T")
    guest.query("INSERT INTO sb0_probe VALUES (7, 'duplicate')", status=b"E", error=True)
    guest.query("SELECT value FROM sb0_probe WHERE id=7", status=b"E", error=True)
    guest.query("ROLLBACK")
    guest.query("SELECT value FROM sb0_probe WHERE id=7", [["committed"]])
    proof.tests.append("failed_transaction_rollback")
    guest.query("THIS IS NOT SQL", error=True)
    guest.query("DELETE FROM sb0_probe WHERE id=8")
    guest.query("SELECT id FROM sb0_probe", [["7"]])
    guest.query("DROP TABLE sb0_probe")
    proof.tests.append("SQL_error_recovery_delete_drop")
    guest.send(packet(b"X"))
    guest.until(b"RAMSQL-SB0K-EXIT" if kind == "sb0k" else b"SB0-ABI-LOADER-EXIT code=0")
    proof.check(b"RAMSQL-SB0K-ERROR" not in guest.transcript and b"RAMSQL-SB0X-ERROR" not in guest.transcript and
                b"SB0-ABI-LOADER-FAULT" not in guest.transcript and b"SB0-ABI-LOADER-REJECT" not in guest.transcript,
                "native application or kernel faulted")
    proof.tests.append("clean_termination_and_no_native_fault")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("sb0x", "sb0k"), required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--qemu", default=os.environ.get("SB0_QEMU", shutil.which("qemu-system-aarch64")))
    parser.add_argument("--loader", type=Path, help="prebuilt public SB0 ABI conformance fixture; otherwise built locally")
    parser.add_argument("--sig", default=os.environ.get("SIG", "sig"))
    parser.add_argument("--sig-lib-dir", type=Path, default=os.environ.get("SIG_LIB_DIR"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.qemu:
        parser.error("qemu-system-aarch64 is required")
    proof = Proof()
    image = args.image.resolve().read_bytes()
    entry = validate_image(image, args.format, proof)
    proof.tests.append("native_container_bounds_and_entry")
    with tempfile.TemporaryDirectory(prefix="ramsql-native-test-") as directory:
        if args.format == "sb0k":
            command = [args.qemu, "-machine", "virt", "-cpu", "cortex-a57", "-m", "128M", "-display", "none",
                       "-monitor", "none", "-serial", "stdio", "-no-reboot",
                       "-device", f"loader,file={args.image.resolve()},addr=0x40200000,force-raw=on",
                       "-device", f"loader,addr={entry:#x},cpu-num=0"]
        else:
            loader = args.loader or build_loader(Path(directory) / "sb0-abi-loader.sb0k", args.sig, args.sig_lib_dir)
            loader_image = loader.resolve().read_bytes()
            loader_entry = validate_image(loader_image, "sb0k", proof)
            metadata = Path(directory) / "image-length.bin"
            metadata.write_bytes(struct.pack("<Q", len(image)))
            reject_invalid_images(args.qemu, loader, loader_entry, image, directory, metadata, proof)
            command = loader_command(args.qemu, loader, loader_entry, args.image, metadata)
        guest = Guest(command, proof)
        try:
            exercise(guest, args.format, proof)
        finally:
            guest.close()
    report = {"passed": True, "target": "aarch64-sb0", "format": args.format,
              "image_sha256": hashlib.sha256(image).hexdigest(), "assertions": proof.assertions, "tests": proof.tests,
              "transport": "uart" if args.format == "sb0k" else "channel", "durable_storage": False, "tcp": False, "scram": False}
    if args.format == "sb0x":
        report["runtime_environment"] = "public single-process SB0 ABI conformance fixture"
        report["full_sb0s_distribution_integration"] = False
        report["loader_sha256"] = hashlib.sha256(loader_image).hexdigest()
        report["loader_source_sha256"] = hashlib.sha256((ROOT / "tools/test_sb0_loader.sig").read_bytes()).hexdigest()
        report["mmu_permissions"] = "EL0 RX/RW pages, read-only BHB and handles, EL1-only kernel and device"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
