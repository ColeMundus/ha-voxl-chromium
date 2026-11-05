"""Exercise the control boundary over actual loopback HTTP connections."""

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

TOKEN = "test-secret-" + "x" * 32


@contextmanager
def serving(backend=None):
    import control

    server = control.ControlServer(("127.0.0.1", 0), TOKEN, backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, method="GET", path="/health", body=None, headers=None):
    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
    supplied = {"Authorization": "Bearer " + TOKEN}
    supplied.update(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body)
        supplied["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=supplied)
    response = conn.getresponse()
    result = response.status, response.getheader("Content-Type"), response.read()
    conn.close()
    return result


class Backend:
    def __init__(self):
        self.calls = []

    def open_url(self, url, new_tab):
        self.calls.append(("open", url, new_tab))

    def restore_window(self):
        self.calls.append(("restore",))

    def screenshot(self):
        return b"\x89PNG\r\n\x1a\nfixture"

    def wait(self, idle_ms, timeout_ms, region):
        self.calls.append(("wait", idle_ms, timeout_ms, region))
        return {"changed": False, "timed_out": False, "seq": 2, "waited_ms": 300}

    def record(self, seconds):
        self.calls.append(("record", seconds))
        return b"\x00\x00\x00\x18ftypmp42fixture"


def test_routes_and_defaults():
    backend = Backend()
    with serving(backend) as server:
        assert request(server, "POST", "/open_url", {"url": "https://example.com"}) == (
            200,
            "application/json",
            b'{"ok":true}',
        )
        assert (
            request(
                server,
                "POST",
                "/open_url",
                {"url": "http://example.com", "new_tab": False},
            )[0]
            == 200
        )
        assert request(server, "POST", "/restore_window", {})[0] == 200
        status, kind, data = request(server, path="/screenshot")
        assert (status, kind, data) == (200, "image/png", backend.screenshot())
        status, kind, data = request(server, "POST", "/wait", {})
        assert status == 200 and json.loads(data)["seq"] == 2
        assert request(server, "POST", "/record", {"seconds": 2})[:2] == (
            200,
            "video/mp4",
        )
    assert backend.calls == [
        ("open", "https://example.com", True),
        ("open", "http://example.com", False),
        ("restore",),
        ("wait", 300, 10000, None),
        ("record", 2),
    ]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/open_url", {}),
        ("/open_url", {"url": "javascript:alert(1)"}),
        ("/open_url", {"url": "https://user:p@example.com"}),
        ("/open_url", {"url": "https://example.com/\n"}),
        ("/open_url", {"url": "https://example.com/\x7f"}),
        ("/open_url", {"url": "https://example.com", "new_tab": 1}),
        ("/open_url", {"url": "https://example.com", "extra": True}),
        ("/record", {"seconds": True}),
        ("/record", {"seconds": 0}),
        ("/record", {"seconds": 61}),
        ("/record", {}),
        ("/wait", {"idle_ms": -1}),
        ("/wait", {"timeout_ms": 60001}),
        ("/wait", {"region": [0, 0, 0, 10]}),
        ("/wait", {"region": "0,0,10,10"}),
        ("/restore_window", {"command": "bad"}),
    ],
)
def test_invalid_input_never_reaches_backend(path, body):
    backend = Backend()
    with serving(backend) as server:
        assert request(server, "POST", path, body)[0] == 400
    assert backend.calls == []


def test_bounded_strict_json_and_generic_dependency_failure():
    backend = Backend()

    def fail(*args):
        raise RuntimeError("secret URL and token must not escape")

    backend.restore_window = fail
    with serving(backend) as server:
        for body in ("{", "[]", '{"seconds":1,"seconds":2}', '{"seconds":NaN}'):
            assert (
                request(
                    server,
                    "POST",
                    "/record",
                    body,
                    {"Content-Type": "application/json"},
                )[0]
                == 400
            )
        assert (
            request(server, "POST", "/record", "{}", {"Content-Type": "text/plain"})[0]
            == 415
        )
        assert (
            request(
                server,
                "POST",
                "/record",
                "x" * 16385,
                {"Content-Type": "application/json"},
            )[0]
            == 413
        )
        assert request(server, "POST", "/restore_window", {}) == (
            502,
            "application/json",
            b'{"error":"dependency unavailable"}',
        )
        assert request(server, "POST", "/health", {})[0] == 405


def test_backend_proxies_bounded_binary_and_validated_wait(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import control

    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            if self.path.startswith("/wait"):
                data, kind = (
                    b'{"changed":true,"timed_out":false,"seq":7,"waited_ms":20}',
                    "application/json",
                )
            elif self.path.startswith("/record"):
                data, kind = b"\x00\x00\x00\x18ftypmp42fixture", "video/mp4"
            else:
                data, kind = b"\x89PNG\r\n\x1a\nfixture", "image/png"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        backend = control.BrowserBackend(upstream_port=upstream.server_port)
        with serving(backend) as server:
            assert request(server, path="/screenshot")[0] == 200
            assert request(server, "POST", "/record", {"seconds": 1})[0] == 200
            assert (
                json.loads(
                    request(server, "POST", "/wait", {"region": [1, 2, 3, 4]})[2]
                )["seq"]
                == 7
            )
            monkeypatch.setattr(control, "MAX_PNG", 4)
            assert request(server, path="/screenshot")[0] == 502
        assert calls[:3] == [
            "/screenshot",
            "/record?seconds=1",
            "/wait?idle=300&timeout=10000&region=1%2C2%2C3%2C4",
        ]
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def test_subprocess_arguments_and_verified_restore(monkeypatch):
    from types import SimpleNamespace

    import control
    from runtime import commands, validate_options

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv == ["wmctrl", "-l"]:
            return SimpleNamespace(stdout="0x0123 0 host Title\n")
        if argv == ["xdotool", "search", "--class", "^Chromium$"]:
            return SimpleNamespace(stdout="291\n")
        if argv == ["xdotool", "getactivewindow"]:
            return SimpleNamespace(stdout="291\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(control.subprocess, "run", run)
    backend = control.BrowserBackend()
    url = "https://example.com/?q=$(bad);echo"
    backend.open_url(url, True)
    assert calls[0][0] == commands(validate_options({}))["chromium"][:-1] + [
        "--new-tab",
        url,
    ]
    backend.open_url(url, False)
    argv = [call[0] for call in calls]
    assert ["wmctrl", "-i", "-a", "0x0123"] in argv
    assert ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", url] in argv
    assert ["xdotool", "key", "--clearmodifiers", "Return"] in argv
    assert all(
        not kwargs.get("shell", False) and kwargs["timeout"] <= 10
        for _, kwargs in calls
    )

    def wrong_focus(argv, **kwargs):
        if argv == ["wmctrl", "-l"]:
            return SimpleNamespace(stdout="0x0123 0 host Title\n")
        if argv == ["xdotool", "search", "--class", "^Chromium$"]:
            return SimpleNamespace(stdout="291\n")
        return SimpleNamespace(stdout="999\n")

    monkeypatch.setattr(control.subprocess, "run", wrong_focus)
    with pytest.raises(RuntimeError):
        backend.restore_window()


def test_mutations_serialized_and_capture_concurrency_limited():
    from concurrent.futures import ThreadPoolExecutor

    backend = Backend()
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3)

    backend.restore_window = block
    with serving(backend) as server, ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(request, server, "POST", "/restore_window", {})
        assert entered.wait(1)
        try:
            assert (
                request(server, "POST", "/open_url", {"url": "https://example.com"})[0]
                == 429
            )
            assert request(server)[0] == 200
        finally:
            release.set()
        assert first.result()[0] == 200
        active = threading.Barrier(3)
        release.clear()

        def capture():
            active.wait(timeout=2)
            assert release.wait(3)
            return b"fixture"

        backend.screenshot = capture
        pending = [pool.submit(request, server, "GET", "/screenshot") for _ in range(2)]
        active.wait(timeout=2)
        try:
            assert request(server, "POST", "/wait", {})[0] == 429
        finally:
            release.set()
        assert all(f.result()[0] == 200 for f in pending)


def test_private_token_is_consumed_and_default_bind_is_internal(monkeypatch, tmp_path):

    import control

    token_file = tmp_path / "token"
    token_file.write_text(TOKEN)
    token_file.chmod(0o600)
    monkeypatch.setattr(control.os, "geteuid", lambda: 1000)
    # Owner validation uses the actual file owner, normally UID1000 in runtime.
    monkeypatch.setattr(control.os, "getuid", lambda: token_file.stat().st_uid)
    calls = []

    class Server:
        def __init__(self, address, token, backend):
            calls.append((address, token, backend))

        def serve_forever(self):
            assert not token_file.exists()

        def server_close(self):
            pass

    with pytest.raises(ValueError):
        control.ControlServer(("127.0.0.1", 0), "")
    monkeypatch.setattr(control, "ControlServer", Server)
    assert control.main(["--token-file", str(token_file), "--language", "en-US"]) == 0
    assert calls[0][0] == ("0.0.0.0", 8100)
    assert calls[0][1] == TOKEN
    assert not token_file.exists()
    token_file.write_text(TOKEN)
    token_file.chmod(0o644)
    assert control.main(["--token-file", str(token_file)]) == 1
    assert len(calls) == 1
    token_file.unlink()


def test_restore_endpoint_returns_ok_not_internal_window_id():
    backend = Backend()
    backend.restore_window = lambda: "0x0123"
    with serving(backend) as server:
        assert request(server, "POST", "/restore_window", {}) == (
            200,
            "application/json",
            b'{"ok":true}',
        )


def test_connection_workers_are_bounded():

    with serving() as server:
        assert server.workers.acquire(blocking=False)
        held = 1
        try:
            while server.workers.acquire(blocking=False):
                held += 1
            assert held == 16
            assert request(server)[0] == 429
        finally:
            for _ in range(held):
                server.workers.release()
        assert request(server)[0] == 200


def test_raw_framing_and_duplicate_auth_are_rejected():
    import socket

    cases = [
        (
            "Authorization: Bearer "
            + TOKEN
            + "\r\nAuthorization: Bearer "
            + TOKEN
            + "\r\nContent-Length: 2",
            401,
        ),
        ("Authorization: Bearer " + TOKEN + "\r\nTransfer-Encoding: chunked", 400),
        ("Authorization: Bearer " + TOKEN + "\r\nContent-Length: -1", 400),
        (
            "Authorization: Bearer "
            + TOKEN
            + "\r\nContent-Length: 2\r\nContent-Length: 2",
            400,
        ),
    ]
    with serving(Backend()) as server:
        for extra, status in cases:
            with socket.create_connection(server.server_address, timeout=2) as conn:
                conn.sendall(
                    (
                        "POST /record HTTP/1.0\r\n"
                        + extra
                        + "\r\nContent-Type: application/json\r\n\r\n{}"
                    ).encode()
                )
                response = http.client.HTTPResponse(conn)
                response.begin()
                assert response.status == status
                assert "error" in json.loads(response.read())


@pytest.mark.parametrize(
    "prefix", [b"GET /health HTTP/1.1", b"GET /health HTTP/1.1\r\nX-Slow: "]
)
def test_slow_headers_have_total_deadline(monkeypatch, prefix):
    import socket
    import time

    import control

    monkeypatch.setattr(control, "HEADER_TIMEOUT", 0.15)
    with serving() as server:
        with socket.create_connection(server.server_address, timeout=2) as conn:
            conn.sendall(prefix)
            # Keep the connection active beyond the total deadline.
            for _ in range(5):
                time.sleep(0.04)
                try:
                    conn.sendall(b"a")
                except OSError:
                    break
            try:
                body = conn.recv(4096)
                assert b"200 OK" not in body
                assert not body or conn.recv(4096) == b""
            except ConnectionResetError:
                pass
        assert request(server)[0] == 200


def test_slow_body_has_total_deadline(monkeypatch):
    import socket
    import time

    import control

    monkeypatch.setattr(control, "BODY_TIMEOUT", 0.15)
    with serving(Backend()) as server:
        with socket.create_connection(server.server_address, timeout=2) as conn:
            conn.sendall(
                (
                    "POST /record HTTP/1.0\r\nAuthorization: Bearer "
                    + TOKEN
                    + "\r\nContent-Type: application/json\r\n"
                    "Content-Length: 100\r\n\r\n"
                ).encode()
            )

            def trickle():
                for _ in range(10):
                    try:
                        conn.sendall(b" ")
                    except OSError:
                        break
                    time.sleep(0.05)

            thread = threading.Thread(target=trickle)
            thread.start()
            started = time.monotonic()
            response = http.client.HTTPResponse(conn)
            response.begin()
            assert response.status == 400
            assert time.monotonic() - started < 0.4
            thread.join()


def test_other_methods_keep_auth_boundary():
    with serving() as server:
        assert request(server, "OPTIONS", headers={"Authorization": ""})[0] == 401
        assert request(server, "DELETE")[0] == 405


@pytest.mark.parametrize(
    "route,status,kind,payload,declared",
    [
        ("/screenshot", 503, "image/png", b"sensitive diagnostic", None),
        ("/screenshot", 302, "image/png", b"redirect", None),
        ("/screenshot", 200, "text/html", b"private content", None),
        ("/screenshot", 200, "image/png", b"not a png", None),
        ("/screenshot", 200, "image/png", b"x" * 33, None),
        ("/screenshot", 200, "image/png", b"\x89PNG\r\n\x1a\n", 20),
        ("/record", 200, "video/mp4", b"not an mp4", None),
        ("/record", 200, "video/mp4", b"x" * 33, None),
        ("/wait", 200, "application/json", b'{"error":"private error"}', None),
    ],
)
def test_upstream_errors_and_invalid_payloads_are_sanitized(
    monkeypatch, route, status, kind, payload, declared
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import control

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if declared is not None:
                self.send_header("Content-Length", str(declared))
            self.end_headers()
            self.wfile.write(payload)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(control, "MAX_PNG", 32)
    monkeypatch.setattr(control, "MAX_MP4", 32)
    try:
        with serving(
            control.BrowserBackend(upstream_port=upstream.server_port)
        ) as server:
            method, body = (
                ("GET", None)
                if route == "/screenshot"
                else ("POST", {"seconds": 1} if route == "/record" else {})
            )
            assert request(server, method, route, body) == (
                502,
                "application/json",
                b'{"error":"dependency unavailable"}',
            )
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


def test_restore_preserves_active_chromium_window(monkeypatch):
    from types import SimpleNamespace

    import control

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv == ["wmctrl", "-l"]:
            return SimpleNamespace(stdout="0x0123 0 host First\n0x0456 0 host Active\n")
        if argv == ["xdotool", "search", "--class", "^Chromium$"]:
            return SimpleNamespace(stdout="291\n1110\n")
        return SimpleNamespace(stdout="1110\n")

    monkeypatch.setattr(control.subprocess, "run", run)
    assert control.BrowserBackend().restore_window() == "0x0456"
    assert ["wmctrl", "-i", "-a", "0x0456"] in calls


def test_authenticated_health_rejects_cross_site_and_wrong_credentials(capsys):
    with serving() as server:
        assert request(server) == (200, "application/json", b'{"ok":true}')
        for headers, status in [
            ({"Authorization": ""}, 401),
            ({"Authorization": "Bearer wrong"}, 401),
            ({"Origin": "null"}, 403),
            ({"Origin": ""}, 403),
        ]:
            code, kind, body = request(server, headers=headers)
            assert code == status
            assert kind == "application/json"
            assert set(json.loads(body)) == {"error"}
        assert request(server, path="/unknown?secret=value")[0] == 404
    assert capsys.readouterr() == ("", "")
