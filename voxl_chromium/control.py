"""Private authenticated automation boundary; deliberately no access logging."""

import argparse
import hmac
import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit

from runtime import commands, validate_options, validate_token

MAX_PNG = 32 * 1024 * 1024
MAX_MP4 = 128 * 1024 * 1024
BODY_TIMEOUT = 5
HEADER_TIMEOUT = 5


class BrowserBackend:
    def __init__(self, language="en-US", upstream_port=6901):
        self.browser = commands(validate_options({"language": language}))["chromium"][
            :-1
        ]
        self.upstream_port = upstream_port

    def run(self, argv, capture=False):
        return subprocess.run(
            argv,
            check=True,
            timeout=10,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def restore_window(self):
        windows = self.run(["wmctrl", "-l"], capture=True).stdout.splitlines()
        # Chromium puts the profile path (which may contain spaces) in res_name.
        # Match X11 res_class independently; wmctrl's text columns cannot parse it.
        chromium = {
            int(value)
            for value in self.run(
                ["xdotool", "search", "--class", "^Chromium$"], capture=True
            ).stdout.split()
        }
        candidates = [
            line.split()[0]
            for line in windows
            if line.split() and int(line.split()[0], 16) in chromium
        ]
        if not candidates:
            raise RuntimeError("browser unavailable")
        window = candidates[0]
        try:
            active = int(
                self.run(["xdotool", "getactivewindow"], capture=True).stdout.strip()
            )
            window = next(
                (item for item in candidates if int(item, 16) == active), window
            )
        except (subprocess.CalledProcessError, ValueError):
            pass
        # Activate by an exact class-derived ID, never a page-controlled title.
        self.run(["wmctrl", "-i", "-a", window])
        self.run(["xdotool", "windowactivate", "--sync", window])
        active = self.run(["xdotool", "getactivewindow"], capture=True).stdout.strip()
        if int(active) != int(window, 16):
            raise RuntimeError("browser unavailable")
        return window

    def open_url(self, url, new_tab):
        if new_tab:
            self.run(self.browser + ["--new-tab", url])
            self.restore_window()
        else:
            self.restore_window()
            self.run(["xdotool", "key", "--clearmodifiers", "ctrl+l"])
            self.run(["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", url])
            self.run(["xdotool", "key", "--clearmodifiers", "Return"])

    def fetch(self, path, kind, limit, timeout):
        deadline = time.monotonic() + timeout
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.upstream_port, timeout=timeout
        )
        try:
            connection.request("GET", path)
            sock = connection.sock
            with connection.getresponse() as response:
                if (
                    response.status != 200
                    or response.getheader("Content-Type", "").split(";")[0] != kind
                ):
                    raise ValueError
                length = response.getheader("Content-Length")
                if length is not None and not 0 <= int(length) <= limit:
                    raise ValueError
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    sock.settimeout(remaining)
                    chunk = response.read1(min(65536, limit + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError
                    if response.isclosed():
                        break
                if length is not None and len(body) != int(length):
                    raise ValueError
                return bytes(body)
        finally:
            connection.close()

    def screenshot(self):
        result = self.fetch("/screenshot", "image/png", MAX_PNG, 10)
        if not result.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError
        return result

    def wait(self, idle_ms, timeout_ms, region):
        params = {"idle": idle_ms, "timeout": timeout_ms}
        if region is not None:
            params["region"] = ",".join(map(str, region))
        result = strict_json(
            self.fetch(
                "/wait?" + urlencode(params),
                "application/json",
                4096,
                timeout_ms / 1000 + 5,
            )
        )
        if (
            not isinstance(result, dict)
            or set(result) != {"changed", "timed_out", "seq", "waited_ms"}
            or any(type(result[k]) is not bool for k in ("changed", "timed_out"))
            or any(
                type(result[k]) is not int or result[k] < 0
                for k in ("seq", "waited_ms")
            )
        ):
            raise ValueError
        return result

    def record(self, seconds):
        result = self.fetch(
            "/record?" + urlencode({"seconds": seconds}),
            "video/mp4",
            MAX_MP4,
            seconds + 10,
        )
        if len(result) < 12 or result[4:8] != b"ftyp":
            raise ValueError
        return result


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token, backend=None):
        if not validate_token(token):
            raise ValueError("automation disabled")
        self.token = token.encode("ascii")
        self.backend = backend
        self.mutation = threading.Lock()
        self.captures = threading.BoundedSemaphore(2)
        self.workers = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(
                    b"HTTP/1.0 429 Too Many Requests\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 16\r\nCache-Control: no-store\r\n\r\n"
                    b'{"error":"busy"}'
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()

    def handle_error(self, request, client_address):
        # Never dump a traceback containing request data.
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, kind="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def handle_one_request(self):
        # Socket timeouts alone reset on each received byte. Close the socket
        # after a total request-line/header budget even if bytes keep arriving.
        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        self.header_timer = threading.Timer(HEADER_TIMEOUT, expire)
        self.header_timer.daemon = True
        self.header_timer.start()
        try:
            super().handle_one_request()
        finally:
            self.header_timer.cancel()

    def parse_request(self):
        try:
            return super().parse_request()
        finally:
            # Capture/record handlers have their own longer bounded deadlines.
            self.header_timer.cancel()

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {"error": "invalid request"})

    def do_GET(self):
        self.dispatch(self.command)

    do_POST = do_GET
    do_HEAD = do_GET
    do_OPTIONS = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_TRACE = do_GET
    do_CONNECT = do_GET

    def dispatch(self, method):
        if "Origin" in self.headers:
            return self.reply(403, {"error": "forbidden"})
        auth = self.headers.get_all("Authorization", [])
        if len(auth) != 1 or not hmac.compare_digest(
            auth[0].encode("utf-8"), b"Bearer " + self.server.token
        ):
            return self.reply(401, {"error": "unauthorized"})
        routes = {
            "/health": "GET",
            "/screenshot": "GET",
            "/open_url": "POST",
            "/restore_window": "POST",
            "/wait": "POST",
            "/record": "POST",
        }
        if self.path not in routes:
            return self.reply(404, {"error": "not found"})
        if method != routes[self.path]:
            return self.reply(405, {"error": "method not allowed"})
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        try:
            data = self.read_json() if method == "POST" else {}
            args = validate_request(self.path, data)
        except RequestError as error:
            return self.reply(error.status, {"error": error.message})
        except (ValueError, TypeError, UnicodeError, RecursionError, TimeoutError):
            return self.reply(400, {"error": "invalid request"})
        gate = (
            self.server.mutation
            if self.path in ("/open_url", "/restore_window")
            else self.server.captures
        )
        if not gate.acquire(blocking=False):
            return self.reply(429, {"error": "busy"})
        try:
            operation = getattr(self.server.backend, self.path[1:])
            result = operation(*args)
            if self.path in ("/open_url", "/restore_window"):
                result = {"ok": True}
            kind = {"/screenshot": "image/png", "/record": "video/mp4"}.get(
                self.path, "application/json"
            )
            self.reply(200, result if result is not None else {"ok": True}, kind)
        except Exception:
            self.reply(502, {"error": "dependency unavailable"})
        finally:
            gate.release()

    def read_json(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError
        length = int(lengths[0])
        if length > 16384:
            raise RequestError(413, "request too large")
        if (
            self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise RequestError(415, "unsupported media type")
        deadline = time.monotonic() + BODY_TIMEOUT
        raw = bytearray()
        while len(raw) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.connection.settimeout(remaining)
            chunk = self.rfile.read1(length - len(raw))
            if not chunk:
                raise ValueError
            raw.extend(chunk)
        self.connection.settimeout(5)
        return strict_json(raw)


class RequestError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(value):
        raise ValueError

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError
    return value


def validate_request(path, data):
    fields = {
        "/open_url": {"url", "new_tab"},
        "/restore_window": set(),
        "/wait": {"idle_ms", "timeout_ms", "region"},
        "/record": {"seconds"},
        "/screenshot": set(),
    }
    if not isinstance(data, dict) or data.keys() - fields[path]:
        raise ValueError
    if path == "/open_url":
        url = data.get("url")
        if (
            not isinstance(url, str)
            or len(url) > 8192
            or any(ord(c) <= 32 or 127 <= ord(c) <= 159 for c in url)
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        _ = parsed.port  # Validate malformed port syntax too.
        new_tab = data.get("new_tab", True)
        if type(new_tab) is not bool:
            raise ValueError
        return url, new_tab
    if path == "/wait":
        idle = integer(data.get("idle_ms", 300), 0, 60000)
        timeout = integer(data.get("timeout_ms", 10000), 1, 60000)
        region = data.get("region")
        if region is not None:
            if not isinstance(region, list) or len(region) != 4:
                raise ValueError
            for i, value in enumerate(region):
                integer(value, 0 if i < 2 else 1, 16384)
        return idle, timeout, region
    if path == "/record":
        return (integer(data.get("seconds"), 1, 60),)
    return ()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--language", default="en-US")
    args = parser.parse_args(argv)
    server = None
    try:
        if os.geteuid() != 1000:
            raise ValueError
        fd = os.open(args.token_file, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
            ):
                raise ValueError
            token = stream.read()
            os.unlink(args.token_file)
        server = ControlServer(("0.0.0.0", 8100), token, BrowserBackend(args.language))
        server.serve_forever()
        return 0
    except Exception:
        print("Automation service failed", file=sys.stderr, flush=True)
        return 1
    finally:
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    sys.exit(main())
