"""Real-container integration checks. No Home Assistant installation is required.

The disposable proxy emulates Supervisor's source IP and Ingress path stripping;
it is not a replacement for a final test on Home Assistant OS/Supervisor.
"""

import argparse
import io
import json
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

PREFIX = "/api/hassio_ingress/smoke-token/"


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(args)}: {result.stderr}")
    return (
        result.stdout + result.stderr if args[0] == "logs" else result.stdout
    ).strip()


def request(url, data=None, method=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def eventually(probe, timeout=60):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = probe()
            if value:
                return value
        except (OSError, ValueError) as error:
            last = error
        time.sleep(0.2)
    raise AssertionError(f"Readiness/condition timeout ({last})")


def run(image):
    suffix = uuid.uuid4().hex[:10]
    network, app, proxy = (f"voxl-{suffix}-{x}" for x in ("net", "app", "proxy"))
    volume = f"voxl-{suffix}-data"
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    created_network = False
    with tempfile.TemporaryDirectory(prefix="voxl-smoke-") as tmp:
        options = Path(tmp) / "options.json"
        options.write_text(
            json.dumps(
                {
                    "start_url": "http://172.30.32.2:8080/fixture",
                    "resize": False,
                    "width": 1280,
                    "height": 720,
                }
            )
        )
        try:
            docker("network", "create", "--subnet", "172.30.32.0/24", network)
            created_network = True
            docker("volume", "create", volume)
            docker(
                "run",
                "-d",
                "--name",
                proxy,
                "--network",
                network,
                "--ip",
                "172.30.32.2",
                "-p",
                "127.0.0.1::8080",
                "-v",
                f"{Path(__file__).with_name('ingress_proxy.py').resolve()}:/proxy.py:ro",
                "--entrypoint",
                "python3",
                image,
                "/proxy.py",
            )
            address = docker("port", proxy, "8080/tcp")
            base = f"http://{address}"
            docker(
                "run",
                "-d",
                "--name",
                app,
                "--network",
                network,
                "--ip",
                "172.30.32.3",
                "--security-opt",
                "no-new-privileges:true",
                "-v",
                f"{volume}:/data",
                "-v",
                f"{options}:/data/options.json:ro",
                "-v",
                f"{Path(__file__).with_name('container_checks.py').resolve()}:/checks.py:ro",
                "-e",
                "SUPERVISOR_TOKEN=must-not-reach-browser",
                image,
            )
            root = base + PREFIX
            eventually(lambda: request(root + "healthz")[0] == 200)
            assert request(root)[0] == 200
            for asset in (
                "client.js",
                "worker.js",
                "audio.js",
                "mic.js",
                "favicon.ico",
            ):
                assert request(root + asset)[0] == 200, asset
            print("PASS: health, HTML and relative assets through Ingress prefix")

            # Even loopback and forged forwarding headers cannot bypass the gate.
            denial = docker("exec", app, "python3", "/checks.py", "denied")
            assert denial == "403", denial
            assert not json.loads(docker("inspect", app))[0]["HostConfig"][
                "PortBindings"
            ]
            print("PASS: untrusted peer denied despite spoofed forwarding headers")

            payload = b"voxl persistent file transfer test\n"
            assert (
                request(
                    root + "files/upload?path=&name=smoke.txt",
                    payload,
                    "PUT",
                    {"X-Voxl-Upload": "1"},
                )[0]
                == 200
            )
            assert request(root + "files/download?path=smoke.txt")[1] == payload
            assert request(root + "files/download?path=../options.json")[0] >= 400
            assert (
                request(
                    root + "files/upload?path=&name=nested.txt&sub=zip-test",
                    payload,
                    "PUT",
                    {"X-Voxl-Upload": "1"},
                )[0]
                == 200
            )
            status, archive = request(root + "files/download?path=zip-test")
            assert status == 200
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                assert zipped.read("zip-test/nested.txt") == payload
            print("PASS: upload/download and file-root traversal rejection")

            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(root + "?stats&present=canvas")
                page.wait_for_function("window.fps > 0", timeout=60000)
                stats = page.evaluate("window.voxlStats")
                print("PASS: actual WebSocket H.264 decoded by WebCodecs", stats)
                page.screenshot(path=str(artifacts / "stream.png"))
                # Click the center of the remote desktop, where the fixture input sits.
                page.mouse.click(640, 360)
                eventually(
                    lambda: json.loads(request(base + "/state")[1])["clicks"] > 0
                )
                page.keyboard.type("voxl-keyboard-ok", delay=40)
                eventually(
                    lambda: (
                        json.loads(request(base + "/state")[1])["typed"]
                        == "voxl-keyboard-ok"
                    ),
                    timeout=20,
                )
                assert json.loads(request(base + "/state")[1])["clicks"] > 0
                assert not errors, errors
                page.screenshot(path=str(artifacts / "input.png"))
                print("PASS: mouse and keyboard reach the real Chromium page")
                audio = page.evaluate("""() => new Promise((resolve, reject) => {
                    const u = new URL('ws', location.href); u.protocol = 'ws:';
                    const s = new WebSocket(u); s.binaryType = 'arraybuffer';
                    const timer = setTimeout(() => { s.close();
                        reject(new Error('No Opus audio received')); }, 15000);
                    s.onopen = () => { s.send('v,0'); s.send('au,1'); };
                    s.onmessage = e => {
                        if (e.data instanceof ArrayBuffer &&
                            new Uint8Array(e.data)[0] === 2) {
                            clearTimeout(timer); s.close();
                            resolve(e.data.byteLength);
                        }
                    };
                })""")
                assert audio > 1
                print("PASS: PulseAudio/Opus audio packets through WebSocket")
                browser.close()

            # Runtime UID and environment: inspect the actual browser, not entrypoint.
            process_check = docker(
                "exec", "--user", "1000:1000", app, "python3", "/checks.py", "identity"
            )
            assert process_check == "ok"
            print("PASS: Chromium UID 1000, no Supervisor token inherited")
            assert json.loads(request(base + "/state")[1])["previousStorage"] == "null"
            settings = json.loads(options.read_text())
            settings["resize"] = True
            options.write_text(json.dumps(settings))
            docker("restart", app)
            eventually(lambda: request(root + "healthz")[0] == 200)
            assert request(root + "files/download?path=smoke.txt")[1] == payload
            profile = docker(
                "exec", app, "test", "-f", "/data/profile/Default/Preferences"
            )
            assert profile == ""
            eventually(
                lambda: (
                    json.loads(request(base + "/state")[1])["previousStorage"]
                    == "persisted"
                )
            )
            print("PASS: browser profile and downloads survive restart")

            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 960, "height": 540})
                page.goto(root + "?present=canvas")
                page.wait_for_function("window.fps > 0", timeout=60000)
                eventually(
                    lambda: (
                        struct.unpack(">II", request(root + "screenshot")[1][16:24])
                        == (960, 540)
                    )
                )
                browser.close()
            print("PASS: dynamic resolution follows viewer size")

            # A dead core service must exit the container, not leave an unhealthy UI.
            docker("exec", app, "python3", "/checks.py", "kill")
            eventually(
                lambda: (
                    json.loads(docker("inspect", app))[0]["State"]["Status"] == "exited"
                ),
                timeout=20,
            )
            assert json.loads(docker("inspect", app))[0]["State"]["ExitCode"] != 0
            print("PASS: core-service failure shuts down the entire container")
        finally:
            for name in (app, proxy):
                logs = docker("logs", name, check=False)
                (artifacts / f"{name}.log").write_text(logs)
                docker("rm", "-f", name, check=False)
            docker("volume", "rm", "-f", volume, check=False)
            if created_network:
                docker("network", "rm", network, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="ha-voxl-chromium:dev")
    run(parser.parse_args().image)
