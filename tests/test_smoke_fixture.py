"""Keep the real-browser fixture's acknowledgments ordered across HTTP threads."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
import smoke
from ingress_proxy import INPUT_STATE, Proxy


def test_input_fixture_does_not_revert_to_an_older_browser_report():
    INPUT_STATE.clear()
    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        thread.start()

        def get(path):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                assert response.status == 200
                return response.read()
            finally:
                connection.close()

        try:
            # The dblclick report can arrive before the preceding click report.
            get("/input_update?" + urlencode({"seq": 2, "double": 1}))
            get("/input_update?" + urlencode({"seq": 1, "double": 0}))
            assert json.loads(get("/input_state")) == {"seq": "2", "double": "1"}
            get("/input_update?" + urlencode({"seq": 2, "double": 0}))
            assert json.loads(get("/input_state")) == {"seq": "2", "double": "1"}
            # A later legitimate report must still be accepted normally.
            get("/input_update?" + urlencode({"seq": 3, "double": 2}))
            assert json.loads(get("/input_state")) == {"seq": "3", "double": "2"}
        finally:
            server.shutdown()
            thread.join(timeout=3)
            INPUT_STATE.clear()


class _Response:
    def __init__(self, status):
        self.status = status


class _Request:
    def __init__(self, page):
        self.page = page

    def get(self, url, timeout):
        if not self.page.results:
            raise AssertionError("request after the scripted results")
        result = self.page.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return _Response(result)


class _Page:
    def __init__(self, results):
        self.results = list(results)
        self.urls = []
        self.request = _Request(self)

    def goto(self, url):
        self.urls.append(url)


def test_application_navigation_waits_for_the_startup_route_replay():
    page = _Page([RuntimeError("net::ERR_NETWORK_CHANGED"), 200])
    smoke.open_loaded_page(
        page, "http://127.0.0.1:9/app?x=1", "http://127.0.0.1:9/healthz"
    )
    assert page.urls == ["data:text/html,ready", "http://127.0.0.1:9/app?x=1"]
    assert page.results == []


def test_a_real_browser_network_error_is_not_waited_out():
    page = _Page([RuntimeError("net::ERR_CONNECTION_REFUSED")])
    with pytest.raises(AssertionError, match="ERR_CONNECTION_REFUSED"):
        smoke.open_loaded_page(
            page, "http://127.0.0.1:9/app", "http://127.0.0.1:9/healthz"
        )
    assert page.urls == ["data:text/html,ready"]
