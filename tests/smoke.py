"""Real-container integration checks. No Home Assistant installation is required.

The disposable proxy emulates Supervisor's source IP and Ingress path stripping;
it is not a replacement for a final test on Home Assistant OS/Supervisor.
"""

import argparse
import base64
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
# Deliberately public, synthetic test canary; never use as a deployment credential.
AUTOMATION_TOKEN = "synthetic-smoke-automation-canary-not-a-real-secret-0123456789"

# The credential travels on stdin, not docker's argv or failure diagnostics.
API_CLIENT = """
import base64, json, sys, urllib.error, urllib.request
p = json.load(sys.stdin)
r = urllib.request.Request(p['url'], method=p['method'],
    data=None if p['body'] is None else json.dumps(p['body']).encode(),
    headers=p['headers'])
try:
    response = urllib.request.urlopen(r, timeout=75)
except urllib.error.HTTPError as error:
    response = error
with response:
    print(json.dumps({'status': response.code,
        'type': response.headers.get('Content-Type', ''),
        'body': base64.b64encode(response.read()).decode()}))
"""

# XIconifyWindow sends WM_CHANGE_STATE to Openbox. Setting _NET_WM_STATE_HIDDEN
# directly does not actually minimize a managed window. Read back ICCCM WM_STATE.
WINDOW_CLIENT = """
import ctypes as c, json, sys
x = c.CDLL('libX11.so.6')
x.XOpenDisplay.argtypes = [c.c_char_p]; x.XOpenDisplay.restype = c.c_void_p
x.XDefaultRootWindow.argtypes = [c.c_void_p]
x.XDefaultRootWindow.restype = c.c_ulong
x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
x.XInternAtom.restype = c.c_ulong
x.XGetWindowProperty.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong,
    c.c_long, c.c_long, c.c_int, c.c_ulong, c.POINTER(c.c_ulong),
    c.POINTER(c.c_int), c.POINTER(c.c_ulong), c.POINTER(c.c_ulong),
    c.POINTER(c.POINTER(c.c_ubyte))]
x.XFree.argtypes = [c.c_void_p]
x.XIconifyWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int]
x.XSync.argtypes = [c.c_void_p, c.c_int]
x.XCloseDisplay.argtypes = [c.c_void_p]
d = x.XOpenDisplay(b':99'); assert d, 'No X display'
def prop(window, name):
    actual, fmt, count, remaining = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong()
    data = c.POINTER(c.c_ubyte)()
    x.XGetWindowProperty(d, window, x.XInternAtom(d, name.encode(), 0),
        0, 4096, 0, 0, c.byref(actual), c.byref(fmt), c.byref(count),
        c.byref(remaining), c.byref(data))
    if not data: return []
    try:
        if fmt.value == 32:
            return list(c.cast(data, c.POINTER(c.c_ulong))[:count.value])
        return c.string_at(data, count.value).decode(errors='replace')
    finally: x.XFree(data)
p = json.load(sys.stdin)
if p.get('window'):
    window = p['window']
else:
    windows = prop(x.XDefaultRootWindow(d), '_NET_CLIENT_LIST')
    matches = [w for w in windows if 'chromium' in str(prop(w, 'WM_CLASS')).lower()]
    assert len(matches) == 1, 'Expected exactly one Chromium window'
    window = matches[0]
if p.get('minimize'):
    assert x.XIconifyWindow(d, window, 0), 'Iconify request failed'
    x.XSync(d, 0)
if p.get('keys'):
    xt = c.CDLL('libXtst.so.6')
    xt.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
    x.XStringToKeysym.argtypes = [c.c_char_p]; x.XStringToKeysym.restype = c.c_ulong
    x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]
    codes = [x.XKeysymToKeycode(d, x.XStringToKeysym(k.encode())) for k in p['keys']]
    assert all(codes), 'Unknown key'
    for code in codes: xt.XTestFakeKeyEvent(d, code, 1, 0)
    for code in reversed(codes): xt.XTestFakeKeyEvent(d, code, 0, 0)
    x.XSync(d, 0)
print(json.dumps({'window': window, 'state': prop(window, 'WM_STATE')}))
x.XCloseDisplay(d)
"""


def exec_python(container, code, payload):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "--user",
            "1000:1000",
            container,
            "python3",
            "-c",
            code,
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode:
        detail = result.stderr.replace(AUTOMATION_TOKEN, "[REDACTED]")
        raise RuntimeError(f"Container Python check failed: {detail}")
    return json.loads(result.stdout)


def automation_request(container, path, body=None, headers=None, host="127.0.0.1"):
    supplied = {"Authorization": "Bearer " + AUTOMATION_TOKEN}
    if body is not None:
        supplied["Content-Type"] = "application/json"
    supplied.update(headers or {})
    supplied = {k: v for k, v in supplied.items() if v is not None}
    result = exec_python(
        container,
        API_CLIENT,
        {
            "url": f"http://{host}:8100{path}",
            "method": "GET" if body is None else "POST",
            "body": body,
            "headers": supplied,
        },
    )
    result["body"] = base64.b64decode(result["body"])
    return result


def automation_checks(app, proxy, base, page, artifacts):
    def api(path, body=None, **kwargs):
        return automation_request(app, path, body, **kwargs)

    def ok(path, body=None):
        response = api(path, body)
        assert response["status"] == 200, (path, response["status"])
        return response

    assert json.loads(ok("/health")["body"]) == {"ok": True}
    remote = automation_request(proxy, "/health", host="172.30.32.3")
    assert remote["status"] == 200
    assert json.loads(remote["body"]) == {"ok": True}
    for headers, expected in (
        ({"Authorization": None}, 401),
        ({"Authorization": ""}, 401),
        ({"Authorization": "Bearer synthetic-wrong-token"}, 401),
        ({"Origin": "http://example.invalid"}, 403),
        ({"Origin": "null"}, 403),
        ({"Origin": ""}, 403),
    ):
        assert api("/health", headers=headers)["status"] == expected
        assert (
            api(
                "/open_url",
                {"url": "http://172.30.32.2:8080/navigation?id=denied"},
                headers=headers,
            )["status"]
            == expected
        )
    for url in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:password@172.30.32.2:8080/navigation",
    ):
        assert api("/open_url", {"url": url})["status"] == 400
    assert json.loads(request(base + "/navigation_state")[1])["acks"] == []
    print(
        "PASS: internal automation auth, Origin/unsafe-URL rejection and network access"
    )

    def navigation_state():
        return json.loads(request(base + "/navigation_state")[1])

    def navigate(identifier, **kwargs):
        ok(
            "/open_url",
            {
                "url": f"http://172.30.32.2:8080/navigation?id={identifier}",
                **kwargs,
            },
        )
        eventually(lambda: identifier in navigation_state()["acks"])
        eventually(lambda: identifier in navigation_state()["visible"])

    def screenshot_color(expected):
        response = ok("/screenshot")
        png = response["body"]
        assert response["type"].startswith("image/png")
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack(">II", png[16:24]) == (1280, 720)
        # Decode the actual API PNG, not the viewer's streamed canvas.
        pixel = page.evaluate(
            """async encoded => {
            const image = await createImageBitmap(await (await fetch(
                'data:image/png;base64,' + encoded)).blob());
            const canvas = document.createElement('canvas');
            canvas.width=image.width; canvas.height=image.height;
            const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0);
            return Array.from(ctx.getImageData(200,550,1,1).data);
        }""",
            base64.b64encode(png).decode(),
        )
        if pixel[:3] != expected:
            return False
        (artifacts / "automation.png").write_bytes(png)
        return True

    navigate("first")  # Default must open a new tab, leaving animated fixture intact.
    navigate("replacement", new_tab=False)
    eventually(lambda: screenshot_color([35, 166, 90]))
    # If false incorrectly created a third tab, the previous tab would be green,
    # not the original blue fixture. Send real X11 keyboard events to Chromium.
    exec_python(app, WINDOW_CLIENT, {"keys": ["Control_L", "Shift_L", "Tab"]})
    eventually(lambda: screenshot_color([18, 86, 148]))
    before = len(navigation_state()["visible"])
    exec_python(app, WINDOW_CLIENT, {"keys": ["Control_L", "Tab"]})
    eventually(lambda: len(navigation_state()["visible"]) > before)
    assert navigation_state()["visible"][-1] == "replacement"
    eventually(lambda: screenshot_color([35, 166, 90]))
    print("PASS: real open_url new-tab/default and current-tab navigation, PNG pixels")

    window = exec_python(app, WINDOW_CLIENT, {"minimize": True})["window"]
    eventually(
        lambda: exec_python(app, WINDOW_CLIENT, {"window": window})["state"][:1] == [3]
    )
    ok("/restore_window", {})
    eventually(
        lambda: exec_python(app, WINDOW_CLIENT, {"window": window})["state"][:1] == [1]
    )
    eventually(lambda: screenshot_color([35, 166, 90]))
    print("PASS: actual ICCCM IconicState minimized window restored to NormalState")

    def wait_result(**kwargs):
        result = json.loads(
            ok("/wait", {"idle_ms": 300, "timeout_ms": 1500, **kwargs})["body"]
        )
        assert type(result["changed"]) is bool
        assert type(result["timed_out"]) is bool
        assert type(result["seq"]) is int
        assert type(result["waited_ms"]) in (int, float)
        return result

    # Allow navigation/title/tab/caret updates to settle before measuring idleness.
    eventually(lambda: not wait_result()["timed_out"], timeout=20)
    stable = wait_result(region=[200, 200, 400, 300])
    assert not stable["timed_out"] and not stable["changed"], stable
    assert stable["waited_ms"] >= 250, stable
    navigate("animated", new_tab=False)
    moving = wait_result(region=[200, 200, 400, 300])
    assert moving["changed"] and moving["timed_out"], moving
    assert moving["waited_ms"] >= 1200, moving
    recording = ok("/record", {"seconds": 1})
    assert recording["type"].startswith("video/mp4")
    assert len(recording["body"]) > 1000
    assert recording["body"][4:8] == b"ftyp"
    (artifacts / "automation.mp4").write_bytes(recording["body"])
    print("PASS: real desktop idle/change wait and one-second MP4 recording")

    # Close only the automation tab: do NOT reload /fixture, since that would
    # overwrite previousStorage before the original persistence assertions run.
    exec_python(app, WINDOW_CLIENT, {"keys": ["Control_L", "w"]})
    eventually(lambda: screenshot_color([18, 86, 148]))
    assert json.loads(request(base + "/state")[1])["previousStorage"] == "null"

    identity = exec_python(
        app,
        """
import json, sys
from pathlib import Path
p = json.load(sys.stdin); found = 0
for proc in Path('/proc').glob('[0-9]*'):
    try:
        if (proc / 'comm').read_text().strip() != 'chromium': continue
        env = (proc / 'environ').read_bytes()
        argv = (proc / 'cmdline').read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError): continue
    found += 1
    assert p['token'].encode() not in env + argv, 'Automation canary leaked to Chromium'
    assert b'automation_token' not in env + argv, 'Automation option leaked to Chromium'
assert found, 'No readable Chromium processes'
print(json.dumps({'checked': found}))
""",
        {"token": AUTOMATION_TOKEN},
    )
    assert identity["checked"] > 0
    assert AUTOMATION_TOKEN not in docker("logs", app)
    print(
        "PASS: automation token absent from actual Chromium argv/environment and logs"
    )


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
                    "automation_token": AUTOMATION_TOKEN,
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
                automation_checks(app, proxy, base, page, artifacts)
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
            settings.pop("automation_token")  # Omitted option disables the API.
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
            for source, host in ((app, "127.0.0.1"), (proxy, "172.30.32.3")):
                disabled = exec_python(
                    source,
                    """
import json, socket, sys
p = json.load(sys.stdin)
with socket.socket() as connection:
    connection.settimeout(2)
    print(json.dumps({'closed': connection.connect_ex((p['host'], 8100)) != 0}))
""",
                    {"host": host},
                )
                assert disabled["closed"], (
                    "Automation API still listening when disabled"
                )
            print("PASS: omitted automation token disables internal/network listener")

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
