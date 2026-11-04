"""Unprivileged desktop command construction and supervision."""

import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
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
}


def validate_options(options):
    if not isinstance(options, dict) or options.keys() - DEFAULTS.keys():
        raise ValueError("Unknown options or options is not an object")
    value = DEFAULTS | options
    for key, low, high in [
        ("width", 640, 3840),
        ("height", 480, 2160),
        ("fps", 1, 60),
        ("bitrate_kbps", 500, 50000),
    ]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    for key in ("audio", "resize"):
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
        "openbox": ["openbox"],
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


def monitor(processes):
    try:
        while True:
            for name, proc in processes.items():
                if proc.poll() is not None:
                    print(f"Service {name} exited ({proc.returncode})", flush=True)
                    return 1
            time.sleep(0.1)
    finally:
        stop_all(processes)


def wait_ready(probe, processes, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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


def main():
    processes = {}

    def interrupted(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        path = Path("/data/options.json")
        options = validate_options(
            json.loads(path.read_text()) if path.exists() else {}
        )
        prepare_data(options)
        plan = commands(options)
        for name in ("xvfb", "pulse", "openbox", "voxl", "nginx", "chromium"):
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
                )
        return monitor(processes)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Startup failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_all(processes)


if __name__ == "__main__":
    sys.exit(main())
