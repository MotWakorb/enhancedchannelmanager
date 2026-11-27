#!/usr/bin/env python3
"""Bounded local BusyBox regression checks; no remote target or security waiver."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import socket
import subprocess
import threading
from pathlib import Path

APPLET_SHA256 = "ad43f2888aa6679930812faef33672c45e98021d3be0d0ed08d257c1d957a968"
BODY = b"ECM BusyBox local regression fixture\n"


def collect(binary: Path) -> dict:
    records, failures = [], []
    applets = subprocess.run(
        [str(binary), "--list-full"], check=True, capture_output=True
    ).stdout
    if (
        hashlib.sha256(applets).hexdigest() != APPLET_SHA256
        or len(applets.splitlines()) != 304
    ):
        failures.append("complete original 304 applet paths differ")
    for value in range(1, 33):
        url = "http://127.0.0.1:9/bounded" + chr(value) + "input"
        result = subprocess.run(
            [str(binary), "wget", "-T", "2", "-O", "/dev/null", url],
            check=False,
            capture_output=True,
            timeout=5,
        )
        record = {
            "control_byte": value,
            "exit": result.returncode,
            "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace"),
        }
        if (
            result.returncode == 0
            or b"Unencoded control character found in the URL!" not in result.stderr
            or b"Connecting to " in result.stderr
        ):
            failures.append(
                "URL control/space byte not rejected before connection: " + str(value)
            )
        records.append(record)
    requests = []
    for target in [
        "/ordinary",
        "/has%20space",
        "/query?a=1&b=two",
        "/%0D%0Aencoded",
        "/caf%C3%A9",
    ]:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(5)
        port = server.getsockname()[1]
        observed, errors = [], []

        def respond(sock: socket.socket, received: list[bytes], exceptions: list[str]) -> None:
            try:
                connection, _ = sock.accept()
                with connection:
                    connection.settimeout(5)
                    raw = b""
                    while b"\r\n\r\n" not in raw and len(raw) < 16384:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                    received.append(raw)
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: "
                        + str(len(BODY)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + BODY
                    )
            except (OSError, TimeoutError) as exc:
                exceptions.append(str(exc))
            finally:
                sock.close()

        thread = threading.Thread(target=respond, args=(server, observed, errors))
        thread.start()
        result = subprocess.run(
            [
                str(binary),
                "wget",
                "-T",
                "3",
                "-O",
                "-",
                f"http://127.0.0.1:{port}" + target,
            ],
            check=False,
            capture_output=True,
            timeout=10,
        )
        thread.join()
        good = (
            result.returncode == 0
            and result.stdout == BODY
            and not errors
            and len(observed) == 1
            and observed[0].startswith(("GET " + target + " HTTP/1.1\r\n").encode())
        )
        if not good:
            failures.append("ordinary/encoded URL regression: " + target)
        requests.append(
            {
                "target": target,
                "exit": result.returncode,
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr": result.stderr.decode(errors="replace"),
                "requests": [raw.decode("latin1") for raw in observed],
                "server_errors": errors,
            }
        )
    date = subprocess.run(
        [str(binary), "date", "-d", "1999-1-2 3:4:5Z"],
        check=False,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "TZ": "UTC"},
        timeout=5,
    )
    if date.returncode != 0 or not date.stdout.startswith(b"Sat Jan  2 03:04:05"):
        failures.append("portable original date-timezone regression")
    return {
        "status": "FAILED" if failures else "PATCHED_BUSYBOX_BOUNDED_RUNTIME_ONLY",
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "applets_sha256": hashlib.sha256(applets).hexdigest(),
        "applet_count": len(applets.splitlines()),
        "control_cases": records,
        "ordinary_and_encoded_cases": requests,
        "date": {
            "exit": date.returncode,
            "stdout": date.stdout.decode(errors="replace"),
            "stderr": date.stderr.decode(errors="replace"),
        },
        "failures": failures,
        "scope": "32 representable C0/space URL rejections, five actual local HTTP transfers, original applet list and date-timezone case only. NUL cannot occur inside a C argv string. Not full source/advisory/image approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("/bin/busybox"))
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    if args.output_file.exists() or args.output_file.is_symlink():
        parser.error("regression evidence must be fresh")
    result = collect(args.binary)
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(result["status"])
    return int(bool(result["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
