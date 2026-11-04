"""TEST ONLY checks executed inside the disposable application container."""

import os
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path


def denied():
    req = urllib.request.Request(
        "http://127.0.0.1:8099/",
        headers={"X-Forwarded-For": "172.30.32.2", "X-Real-IP": "172.30.32.2"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 403
        print("403")
        return
    raise AssertionError("Untrusted peer bypassed ingress authorization")


def browser_identity():
    found = 0
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if (proc / "comm").read_text().strip() != "chromium":
                continue
            status = (proc / "status").read_text()
            env = (proc / "environ").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        found += 1
        assert "Uid:\t1000\t1000\t1000\t1000" in status
        assert b"SUPERVISOR_TOKEN=" not in env
    assert found
    print("ok")


def kill_voxl():
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if (proc / "comm").read_text().strip() == "voxl":
                os.kill(int(proc.name), signal.SIGKILL)
                return
        except (FileNotFoundError, ProcessLookupError):
            continue
    raise AssertionError("voxl not running")


if __name__ == "__main__":
    {"denied": denied, "identity": browser_identity, "kill": kill_voxl}[sys.argv[1]]()
