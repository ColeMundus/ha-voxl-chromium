"""Unprivileged desktop command construction and supervision."""

import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULTS = {
    "start_url": "https://www.home-assistant.io",
    "width": 1280,
    "height": 720,
    "fps": 30,
    "bitrate_kbps": 8000,
    "audio": True,
    "resize": True,
    "resize_scale": 1.0,
    "timezone": "Etc/UTC",
    "language": "en-US",
    "automation_token": "",
    "automation_enabled": False,
}


def validate_options(options):
    if not isinstance(options, dict) or options.keys() - DEFAULTS.keys():
        raise ValueError("Unknown options or options is not an object")
    value = DEFAULTS | options
    validate_token(value["automation_token"])
    for key, low, high in [
        ("width", 640, 3840),
        ("height", 480, 2160),
        ("fps", 1, 60),
        ("bitrate_kbps", 500, 50000),
    ]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("audio", "resize", "automation_enabled"):
        if type(value[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    scale = value["resize_scale"]
    if (
        type(scale) not in (int, float)
        or not math.isfinite(scale)
        or not 0.5 <= scale <= 2
    ):
        raise ValueError("resize_scale must be between 0.5 and 2")
    url = value["start_url"]
    if not isinstance(url, str) or any(ord(c) < 32 for c in url):
        raise ValueError("start_url must be an HTTP(S) URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("start_url must be an HTTP(S) URL without credentials")
    if not isinstance(value["language"], str) or not re.fullmatch(
        r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", value["language"]
    ):
        raise ValueError("language must be a language tag such as en-US")
    try:
        ZoneInfo(value["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError("timezone must be an IANA timezone") from error
    return value


def validate_token(token):
    if not isinstance(token, str) or (
        token and (len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token))
    ):
        raise ValueError(
            "automation_token must be empty or at least 32 printable ASCII "
            "characters without spaces"
        )
    return token


def resolve_automation_token(options, data=Path("/data")):
    """Persist only opt-in generated secrets, with exclusive/no-follow access."""
    if options["automation_token"] or not options["automation_enabled"]:
        return options["automation_token"]
    path = data / "automation-token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise ValueError("Invalid automation token file") from None
            token = stream.read(4097)
        if not token or len(token) > 4096:
            raise ValueError("Invalid automation token file") from None
        return validate_token(token)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        token = secrets.token_urlsafe(48)
        stream.write(token)
        stream.flush()
        os.fsync(stream.fileno())
    return token


class BrowserManager:
    """Own browser and profile-forwarder PIDs, independent of critical services."""

    def __init__(self, stopped=None):
        self.processes = {}
        self.lock = threading.RLock()
        self.closed = False
        self.serial = 0
        self.stopped = stopped

    def launch(self, argv):
        with self.lock:
            if self.closed or (self.stopped is not None and self.stopped.is_set()):
                raise RuntimeError("Browser stopping")
            # Reap closed browsers/forwarders without abandoning group ownership.
            for name, child in list(self.processes.items()):
                if child.poll() is not None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    del self.processes[name]
            self.serial += 1
            proc = subprocess.Popen(argv, start_new_session=True)
            self.processes[f"chromium-{self.serial}"] = proc
            return proc

    def stop(self):
        with self.lock:
            self.closed = True
            stop_all(self.processes)


def prepare_desktop_menu(directory):
    """Keep standard WM bindings, replacing only its root menu."""
    ns = "{http://openbox.org/3.4/rc}"
    config = ET.parse("/etc/xdg/openbox/rc.xml")
    menu = config.getroot().find(ns + "menu")
    for item in list(menu):
        if item.tag == ns + "file":
            menu.remove(item)
    ET.SubElement(menu, ns + "file").text = str(directory / "menu.xml")
    config.write(directory / "openbox.xml")
    root = ET.Element("openbox_menu", xmlns="http://openbox.org/3.4/menu")
    menu = ET.SubElement(root, "menu", id="root-menu", label="Chromium")
    item = ET.SubElement(menu, "item", label="Open / restore Chromium")
    action = ET.SubElement(item, "action", name="Execute")
    ET.SubElement(
        action, "command"
    ).text = (
        f"{sys.executable} {Path(__file__).with_name('control.py')} --restore-local"
    )
    ET.ElementTree(root).write(directory / "menu.xml")


def commands(options):
    return {
        "xvfb": [
            "Xvfb",
            ":99",
            "-screen",
            "0",
            f"{options['width']}x{options['height']}x24",
            "-nolisten",
            "tcp",
            "-shmem",
            "+extension",
            "RANDR",
        ],
        "pulse": [
            "pulseaudio",
            "--daemonize=no",
            "--exit-idle-time=-1",
            "--load=module-null-sink sink_name=output",
        ],
        "openbox": ["openbox", "--config-file", "/tmp/browser-runtime/openbox.xml"],
        "chromium": [
            "chromium",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--disable-session-crashed-bubble",
            "--user-data-dir=/data/profile",
            "--start-maximized",
            f"--lang={options['language']}",
            options["start_url"],
        ],
        "voxl": [
            "voxl",
            "--addr=127.0.0.1",
            "--port=6901",
            "--enable-basic-auth=false",
            "--title=Chromium",
            f"--framerate={options['fps']}",
            f"--max-kbps={options['bitrate_kbps']}",
            "--min-kbps=500",
            f"--audio-enabled={str(options['audio']).lower()}",
            "--audio-device-name=output.monitor",
            f"--enable-resize={str(options['resize']).lower()}",
            f"--resize-scale={options['resize_scale']}",
            "--files-root=/data/Downloads",
            "--upload-dir=.",
            "--max-upload-mb=100",
            "--microphone-enabled=false",
        ],
        "nginx": [
            "/usr/sbin/nginx",
            "-c",
            "/opt/addon/nginx.conf",
            "-g",
            "daemon off;",
        ],
    }


def stop_all(processes):
    """Stop dependents before X/audio and allow Chromium to flush its profile."""
    deadline = time.monotonic() + 8
    for proc in reversed(list(processes.values())):
        if proc.poll() is None:
            # Signal the leader first; killing renderers with it interrupts saves.
            proc.terminate()
            try:
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # A group leader may exit while its descendants remain alive.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def monitor(processes, threads=(), cleanup=True, stopped=None):
    try:
        while True:
            if stopped is not None and stopped.is_set():
                return 0
            if any(not thread.is_alive() for thread in threads):
                print("Control service exited", flush=True)
                return 1
            for name, proc in processes.items():
                if not name.startswith("chromium") and proc.poll() is not None:
                    print(f"Service {name} exited ({proc.returncode})", flush=True)
                    return 1
            time.sleep(0.1)
    finally:
        if cleanup:
            stop_all(processes)


def wait_ready(probe, processes, timeout=30, stopped=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopped is not None and stopped.is_set():
            raise SystemExit(0)
        if any(p.poll() is not None for p in processes.values()):
            raise RuntimeError("Service exited during startup")
        try:
            if probe():
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Service readiness timed out")


def prepare_data(options):
    """Only chown the data root as root; handle browser files after dropping UID."""
    data = Path("/data")
    data.mkdir(exist_ok=True)
    if data.is_symlink():
        raise ValueError("/data must not be a symlink")
    if os.geteuid() == 0:
        os.chown(data, 1000, 1000)
        os.setgroups([])
        os.setgid(1000)
        os.setuid(1000)
    os.umask(0o077)
    for path in (data / "profile", data / "Downloads", Path("/tmp/browser-runtime")):
        if path.is_symlink():
            raise ValueError(f"{path} must not be a symlink")
        path.mkdir(mode=0o700, exist_ok=True)
    # Do not inherit Supervisor credentials, proxy credentials or host env vars.
    os.environ.clear()
    os.environ.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/home/browser",
            "DISPLAY": ":99",
            "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": "/tmp/browser-runtime",
            "TZ": options["timezone"],
            "PULSE_SINK": "output",
            "PYTHONUNBUFFERED": "1",
        }
    )
    default = data / "profile/Default"
    default.mkdir(exist_ok=True)
    prefs = default / "Preferences"
    existing = json.loads(prefs.read_text()) if prefs.exists() else {}
    existing.setdefault("download", {}).update(
        {
            "default_directory": "/data/Downloads",
            "prompt_for_download": False,
        }
    )
    prefs.write_text(json.dumps(existing))


def start_discovery():
    """Start an optional narrow publisher with a private pipe and clean environment."""
    try:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("supervisor_discovery.py"))],
            stdin=subprocess.PIPE,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            start_new_session=True,
            **(
                {"user": 1000, "group": 1000, "extra_groups": []}
                if os.geteuid() == 0
                else {}
            ),
        )
    except OSError:
        print("Optional Supervisor discovery unavailable", flush=True)
        return None


def main():
    processes = {}
    stopped = threading.Event()
    browser = BrowserManager(stopped)
    servers = []
    threads = []
    discovery = None

    def interrupted(signum, frame):
        # Never unwind through Popen registration with a live unowned child.
        stopped.set()

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        path = Path("/data/options.json")
        options = validate_options(
            json.loads(path.read_text()) if path.exists() else {}
        )
        # Narrow publisher retains credentials privately, never in argv/environment.
        supervisor_token = os.environ.pop("SUPERVISOR_TOKEN", "")
        os.environ.pop("HASSIO_TOKEN", None)
        if supervisor_token:
            discovery = start_discovery()
        prepare_data(options)
        options["automation_token"] = resolve_automation_token(options)
        prepare_desktop_menu(Path(os.environ["XDG_RUNTIME_DIR"]))
        plan = commands(options)
        for name in ("xvfb", "pulse", "openbox", "voxl", "nginx"):
            print(f"Starting {name}", flush=True)
            processes[name] = subprocess.Popen(plan[name], start_new_session=True)
            if name == "xvfb":
                wait_ready(
                    lambda: (
                        subprocess.run(
                            ["xdpyinfo"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                        ).returncode
                        == 0
                    ),
                    processes,
                    stopped=stopped,
                )
            elif name == "pulse":
                wait_ready(
                    lambda: (
                        subprocess.run(
                            ["pactl", "info"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                        ).returncode
                        == 0
                    ),
                    processes,
                    stopped=stopped,
                )
            elif name in ("voxl", "nginx"):
                port = 6901 if name == "voxl" else 8099
                wait_ready(
                    lambda port=port: (
                        urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/healthz",
                            timeout=2,
                        ).status
                        == 200
                    ),
                    processes,
                    stopped=stopped,
                )
        from control import (
            LOCAL_SOCKET,
            BrowserBackend,
            ControlServer,
            LocalRestoreServer,
        )

        backend = BrowserBackend(
            options["language"], launcher=browser.launch, start_url=options["start_url"]
        )
        browser.launch(plan["chromium"])
        Path(LOCAL_SOCKET).unlink(missing_ok=True)
        servers.append(LocalRestoreServer(LOCAL_SOCKET, backend))
        if options["automation_token"]:
            servers.append(
                ControlServer(("0.0.0.0", 8100), options["automation_token"], backend)
            )
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        if discovery is not None:
            token = options["automation_token"]

            def api_ready():
                req = urllib.request.Request(
                    "http://127.0.0.1:8100/health",
                    headers={"Authorization": "Bearer " + token},
                )
                with urllib.request.urlopen(req, timeout=2) as response:
                    return response.status == 200

            if token:
                wait_ready(api_ready, processes, stopped=stopped)
            try:
                discovery.stdin.write(
                    json.dumps(
                        {"supervisor_token": supervisor_token, "token": token}
                    ).encode()
                )
                discovery.stdin.close()
            except OSError:
                print("Optional Supervisor discovery unavailable", flush=True)
        supervisor_token = ""
        return monitor(processes, threads, cleanup=False, stopped=stopped)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Startup failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        # Block future launches before stopping browsers, then X/audio.
        browser.stop()
        for server, thread in zip(servers, threads, strict=False):
            server.shutdown()
            thread.join(timeout=1)
        for server in servers:
            server.server_close()
        stop_all(processes)
        if discovery is not None:
            if discovery.stdin and not discovery.stdin.closed:
                discovery.stdin.close()
            stop_all({"discovery": discovery})


if __name__ == "__main__":
    sys.exit(main())
