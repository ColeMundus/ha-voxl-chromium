"""Narrow optional Supervisor publisher; credentials arrive only on private stdin.

No Core/registry APIs, no inherited environment, no child processes, no request logs.
The helper is disposable and runs separately so retries cannot delay the desktop.
"""

import http.client
import json
import os
import re
import signal
import stat
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

SERVICE = "voxl_chromium"
STATE = Path("/data/discovery.json")


class SupervisorUnavailable(OSError):
    """An explicit rejection is safe to retry, unlike an ambiguous POST timeout."""


class PublicationUncertain(Exception):
    """Do not repeat a POST which might already have created a discovery entry."""


class SupervisorClient:
    def __init__(self, token, base="http://supervisor"):
        self.token = token
        self.base = urlsplit(base)

    def request(self, method, path, data=None):
        connection = http.client.HTTPConnection(
            self.base.hostname, self.base.port, timeout=3
        )
        try:
            body = None if data is None else json.dumps(data).encode()
            connection.request(
                method,
                path,
                body,
                {
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            with connection.getresponse() as response:
                if method == "DELETE" and response.status == 404:
                    return {}
                if response.status != 200:
                    raise SupervisorUnavailable("Supervisor unavailable")
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Supervisor response too large")
                result = json.loads(raw)
                if result.get("result") != "ok" or not isinstance(
                    result.get("data"), dict
                ):
                    raise ValueError("Supervisor response invalid")
                return result["data"]
        except SupervisorUnavailable:
            raise
        except (OSError, ValueError, http.client.HTTPException) as error:
            if method == "POST":
                raise PublicationUncertain from None
            raise error
        finally:
            connection.close()


def internal_hostname(info):
    host = info.get("hostname") or info.get("slug", "").replace("_", "-")
    if not isinstance(host, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", host
    ):
        raise ValueError("Invalid internal hostname")
    return host


def read_state(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("Invalid discovery state")
        value = json.loads(stream.read(4096))
    # Only our exact stored service/add-on UUID is eligible for deletion.
    if value.get("service") != SERVICE:
        raise ValueError("Invalid discovery state")
    validate_uuid(value["uuid"])
    return value


def validate_uuid(value):
    # Supervisor uses uuid4().hex, not the hyphenated UUID representation. Keep
    # its exact opaque identifier: normalization makes scoped DELETE miss it.
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        value,
    ):
        raise ValueError("Invalid discovery UUID")
    return value


def write_state(path, value):
    fd, name = tempfile.mkstemp(prefix=".discovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def publish(client, token, state=STATE):
    info = client.request("GET", "/addons/self/info")
    host = internal_hostname(info)
    previous = read_state(state)
    if previous is not None:
        if previous.get("slug") != info["slug"]:
            raise ValueError("Discovery state belongs to another add-on")
    if not token:
        if previous is not None:
            client.request("DELETE", "/discovery/" + previous["uuid"])
            state.unlink(missing_ok=True)
        return
    # Supervisor upserts by (add-on, service), including changed credentials.
    # Repeated publication preserves its UUID; no broad registry reads/deletes.
    result = client.request(
        "POST",
        "/discovery",
        {
            "service": SERVICE,
            "config": {"host": host, "port": 8100, "token": token},
        },
    )
    try:
        uuid = validate_uuid(result["uuid"])
        write_state(state, {"service": SERVICE, "slug": info["slug"], "uuid": uuid})
    except (OSError, ValueError, KeyError, TypeError):
        # No registry enumeration is permitted. Failing closed here is safer than
        # creating duplicates after a lost acknowledgement or unwritable state.
        raise PublicationUncertain from None


def retry_publish(operation, stopped, delays=(0, 1, 2, 4, 8, 16, 30, 30)):
    for delay in delays:
        if stopped.wait(delay):
            return False
        try:
            operation()
            return True
        except PublicationUncertain:
            break
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            pass
    # Never print exceptions: response bodies and URLs may carry secrets.
    print(
        "Optional Supervisor discovery unavailable; desktop remains running", flush=True
    )
    return False


def main():
    os.environ.clear()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        credentials = json.loads(sys.stdin.buffer.read(16384))
        client = SupervisorClient(credentials["supervisor_token"])
        retry_publish(lambda: publish(client, credentials["token"]), stopped)
        return 0
    except Exception:
        print(
            "Optional Supervisor discovery unavailable; desktop remains running",
            flush=True,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
