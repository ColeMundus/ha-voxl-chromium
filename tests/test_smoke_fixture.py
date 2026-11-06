"""Keep the real-browser fixture's acknowledgments ordered across HTTP threads."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from ingress_proxy import INPUT_STATE, Proxy


def test_input_fixture_does_not_revert_to_an_older_browser_report():
    INPUT_STATE.clear()
    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
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
