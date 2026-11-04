"""Ask the guest to power off and wait until QEMU closes its QMP channel."""

import json
import socket
import time


def request_poweroff(path, timeout=60):
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(timeout)
        conn.connect(path)
        with conn.makefile("rb") as stream:
            greeting = json.loads(stream.readline())
            if "QMP" not in greeting:
                raise RuntimeError("Missing QMP greeting")
            for command in ("qmp_capabilities", "system_powerdown"):
                conn.sendall(json.dumps({"execute": command}).encode() + b"\n")
                while True:
                    conn.settimeout(max(0.01, deadline - time.monotonic()))
                    reply = json.loads(stream.readline())
                    if "error" in reply:
                        raise RuntimeError(f"QMP rejected {command}")
                    if "return" in reply:
                        break
            # Do not return when the command is merely accepted: systemd would
            # terminate QEMU before the guest has flushed its disk and caches.
            while True:
                conn.settimeout(max(0.01, deadline - time.monotonic()))
                if not stream.readline():
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Guest did not power off")


if __name__ == "__main__":
    request_poweroff("/run/gh-voxl-vm/qmp.sock")
