"""Scoped Supervisor publication, using only the add-on's basic permissions."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_publication_upserts_own_service_and_uses_actual_hostname(tmp_path):
    import supervisor_discovery as discovery

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(("GET", self.path, None))
            self.reply(
                {"slug": "abc_voxl_chromium", "hostname": "actual-internal-host"}
            )

        def do_DELETE(self):
            calls.append(("DELETE", self.path, None))
            self.reply({})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, data))
            assert self.headers["Authorization"] == "Bearer supervisor-secret"
            self.reply({"uuid": "12345678123412341234123456789abc"})

        def reply(self, data):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"result": "ok", "data": data}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = discovery.SupervisorClient(
            "supervisor-secret", base=f"http://127.0.0.1:{server.server_port}"
        )
        state = tmp_path / "discovery.json"
        discovery.publish(client, "a" * 43, state)
        discovery.publish(client, "b" * 43, state)
        assert calls == [
            ("GET", "/addons/self/info", None),
            (
                "POST",
                "/discovery",
                {
                    "service": "voxl_chromium",
                    "config": {
                        "host": "actual-internal-host",
                        "port": 8100,
                        "token": "a" * 43,
                    },
                },
            ),
            ("GET", "/addons/self/info", None),
            (
                "POST",
                "/discovery",
                {
                    "service": "voxl_chromium",
                    "config": {
                        "host": "actual-internal-host",
                        "port": 8100,
                        "token": "b" * 43,
                    },
                },
            ),
        ]
        assert "token" not in state.read_text()
        assert (
            json.loads(state.read_text())["uuid"] == "12345678123412341234123456789abc"
        )
        assert state.stat().st_mode & 0o777 == 0o600
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_slug_fallback_and_no_arbitrary_hostname():
    import supervisor_discovery as discovery

    assert (
        discovery.internal_hostname({"slug": "abc_voxl_chromium"})
        == "abc-voxl-chromium"
    )
    with pytest.raises(ValueError):
        discovery.internal_hostname({"hostname": "http://evil/path", "slug": ""})


def test_disabled_automation_removes_only_own_saved_message(tmp_path):
    import supervisor_discovery as discovery

    state = tmp_path / "discovery.json"
    discovery.write_state(
        state,
        {
            "service": "voxl_chromium",
            "slug": "abc_voxl_chromium",
            "uuid": "12345678123412341234123456789abc",
        },
    )
    calls = []

    class Client:
        def request(self, method, path, data=None):
            calls.append((method, path))
            return {"slug": "abc_voxl_chromium"} if method == "GET" else {}

    discovery.publish(Client(), "", state)
    assert calls == [
        ("GET", "/addons/self/info"),
        ("DELETE", "/discovery/12345678123412341234123456789abc"),
    ]
    assert not state.exists()
    target = tmp_path / "target"
    target.write_text("do not touch")
    state.symlink_to(target)
    with pytest.raises(OSError):
        discovery.publish(Client(), "x" * 43, state)
    assert target.read_text() == "do not touch"
    assert calls[-1] == ("GET", "/addons/self/info")


def test_uncertain_publication_never_retries_or_logs_secrets(capsys):
    import supervisor_discovery as discovery

    calls = []

    def uncertain():
        calls.append(1)
        raise discovery.PublicationUncertain("super-secret-do-not-log")

    assert not discovery.retry_publish(uncertain, threading.Event(), delays=(0, 0, 0))
    assert calls == [1]
    assert "super-secret" not in repr(capsys.readouterr())


def test_retry_is_bounded_and_cancellable():
    import supervisor_discovery as discovery

    calls = []

    def fail():
        calls.append(1)
        raise OSError("secret must not be logged")

    stopped = threading.Event()
    assert not discovery.retry_publish(fail, stopped, delays=(0, 0, 0))
    assert len(calls) == 3
    stopped.set()
    assert not discovery.retry_publish(fail, stopped, delays=(0,))
    assert len(calls) == 3
