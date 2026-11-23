"""Private authenticated automation boundary; deliberately no access logging."""

import contextlib
import ctypes
import hmac
import http.client
import json
import os
import select
import socket
import socketserver
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
WINDOW_TIMEOUT = 8
LOCAL_SOCKET = "/tmp/browser-runtime/browser.sock"
_X_ERROR_HANDLER = None


def unicode_keysym(char):
    """Keysym xdotool looks up when a character is not already in its map."""
    codepoint = ord(char)
    if codepoint < 0x100:
        return codepoint
    return 0x01000000 + codepoint


def x_time_after(later, earlier):
    elapsed = (later - earlier) & 0xFFFFFFFF
    return 0 < elapsed < 0x80000000


class KeymapSlots:
    """Bind unmapped characters once, and keep that map stable while typing.

    xdotool types a missing character by pointing one scratch keycode at it and
    clearing the keycode before the client reads the key. Prebinding every
    missing character means xdotool never touches that scratch keycode. A slot
    is reused only after the browser's user time has moved past the keys that
    used it.
    """

    def __init__(self, display, ack_timeout=5):
        self.display = display
        self.ack_timeout = ack_timeout
        self.owned = {}
        self.pending = {}
        self.pool = None
        self.trace = []

    def chunks(self, text):
        self._ensure_pool()
        external = self._external_keysyms()
        chunks = []
        current = []
        occupants = set()
        for char in text:
            codepoint = ord(char)
            if not self._in_map(char, external):
                if codepoint not in occupants and (
                    not self.pool or len(occupants) >= len(self.pool)
                ):
                    if not current:
                        raise RuntimeError("no keymap slots for unmapped character")
                    chunks.append("".join(current))
                    current = []
                    occupants = set()
                occupants.add(codepoint)
            current.append(char)
        if current:
            chunks.append("".join(current))
        return chunks or [""]

    def _external_keysyms(self):
        mapped = set(self.display.mapped_keysyms())
        for codepoint in self.owned:
            mapped.discard(unicode_keysym(chr(codepoint)))
            mapped.discard(codepoint)
        return mapped

    def arm(self, text, window):
        """Bind any character in text that the server cannot type yet.

        Returns True when the map changed and the caller must wait until the
        browser has observed that change before sending keys.
        """
        self._ensure_pool()
        mapped = self.display.mapped_keysyms()
        needed = []
        seen = set()
        for char in text:
            codepoint = ord(char)
            if codepoint in seen or self._satisfied(char, mapped):
                continue
            seen.add(codepoint)
            needed.append(char)
        if not needed:
            return False
        if len(needed) > len(self.pool):
            raise RuntimeError("type_text chunk exceeds keymap slots")
        used = {ord(char) for char in text}
        free = [keycode for keycode in self.pool if keycode not in self.owned.values()]
        reusable = [
            keycode
            for codepoint, keycode in self.owned.items()
            if codepoint not in used
        ]
        mapping = {}
        for char in needed:
            if free:
                keycode = free.pop(0)
            elif reusable:
                keycode = reusable.pop(0)
                self._wait(keycode, window)
                for codepoint, owner in list(self.owned.items()):
                    if owner == keycode:
                        del self.owned[codepoint]
            else:
                raise RuntimeError("type_text chunk exceeds keymap slots")
            mapping[keycode] = unicode_keysym(char)
            self.owned[ord(char)] = keycode
        self.trace.append(("bind", dict(mapping)))
        self.display.bind_level0(mapping)
        return True

    def stamp(self, window):
        return self.display.server_time(window)

    def wait_for(self, window, stamps):
        self._wait_until(window, stamps)

    def note_burst(self, text, stamp):
        for char in dict.fromkeys(text):
            keycode = self.owned.get(ord(char))
            if keycode is not None:
                self.pending[keycode] = stamp

    def _wait(self, keycode, window):
        stamp = self.pending.get(keycode)
        if stamp is None:
            return
        self.trace.append(("wait", keycode))
        self._wait_until(window, (stamp,))
        self.pending.pop(keycode, None)

    def _wait_until(self, window, stamps):
        deadline = time.monotonic() + self.ack_timeout
        while True:
            user_time = self.display.user_time(window)
            if user_time is not None and all(
                x_time_after(user_time, stamp) for stamp in stamps
            ):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("keymap slot still awaiting browser acknowledgment")
            time.sleep(0.01)

    def _satisfied(self, char, mapped):
        return self._in_map(char, mapped)

    def _in_map(self, char, mapped):
        return unicode_keysym(char) in mapped or ord(char) in mapped

    def _ensure_pool(self):
        if self.pool is None:
            self.pool = list(self.display.empty_keycodes())


class _XPropertyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("atom", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("state", ctypes.c_int),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


class X11Display:
    """Xlib keyboard map used to prebind characters before xdotool runs."""

    def __init__(self, name=None):
        self.name = name
        self._lib = ctypes.CDLL("libX11.so.6")
        lib = self._lib
        lib.XInitThreads.restype = ctypes.c_int
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XCreateSimpleWindow.restype = ctypes.c_ulong
        lib.XCreateSimpleWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        lib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XChangeProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XConnectionNumber.restype = ctypes.c_int
        lib.XConnectionNumber.argtypes = [ctypes.c_void_p]
        lib.XPending.restype = ctypes.c_int
        lib.XPending.argtypes = [ctypes.c_void_p]
        lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.XDisplayKeycodes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
        lib.XGetKeyboardMapping.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.XChangeKeyboardMapping.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_int,
        ]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        global _X_ERROR_HANDLER  # noqa: PLW0603 - keep the ctypes callback alive
        if _X_ERROR_HANDLER is None:
            prototype = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

            def _ignore(_display, _event):
                return 0

            _X_ERROR_HANDLER = prototype(_ignore)
            lib.XSetErrorHandler(_X_ERROR_HANDLER)
        lib.XInitThreads()
        opened = lib.XOpenDisplay(None if name is None else name.encode())
        if not opened:
            raise RuntimeError("browser unavailable")
        self._dpy = opened
        root = lib.XDefaultRootWindow(self._dpy)
        self._clock = lib.XCreateSimpleWindow(self._dpy, root, 0, 0, 1, 1, 0, 0, 0)
        lib.XSelectInput(self._dpy, self._clock, 1 << 22)
        self._clock_atom = lib.XInternAtom(
            self._dpy,
            b"_VOXL_KEYMAP_TIME",
            False,  # noqa: FBT003 - ctypes only_if_exists
        )
        self._user_atom = None

    def empty_keycodes(self):
        low, high, per, symbols = self._mapping()
        empty = []
        for keycode in range(low, high + 1):
            base = (keycode - low) * per
            if all(int(symbols[base + level]) == 0 for level in range(per)):
                empty.append(keycode)
        self._lib.XFree(symbols)
        return empty

    def mapped_keysyms(self):
        low, high, per, symbols = self._mapping()
        found = {int(symbols[index]) for index in range((high - low + 1) * per)}
        found.discard(0)
        self._lib.XFree(symbols)
        return found

    def bind_level0(self, mapping):
        if not mapping:
            return
        low, _high, per, symbols = self._mapping()
        start, end = min(mapping), max(mapping)
        count = end - start + 1
        buffer = (ctypes.c_ulong * (count * per))()
        for keycode in range(start, end + 1):
            src = (keycode - low) * per
            dst = (keycode - start) * per
            for level in range(per):
                buffer[dst + level] = symbols[src + level]
            if keycode in mapping:
                buffer[dst] = mapping[keycode]
        self._lib.XFree(symbols)
        self._lib.XChangeKeyboardMapping(self._dpy, start, per, buffer, count)
        self._lib.XSync(self._dpy, False)  # noqa: FBT003 - ctypes discard flag
        low, _high, per, symbols = self._mapping()
        try:
            for keycode, keysym in mapping.items():
                if int(symbols[(keycode - low) * per]) != keysym:
                    raise RuntimeError("keymap update was not accepted")
        finally:
            self._lib.XFree(symbols)

    def server_time(self, window):
        del window
        event = (ctypes.c_char * 256)()
        self._lib.XChangeProperty(
            self._dpy,
            self._clock,
            self._clock_atom,
            31,
            8,
            2,
            None,
            0,
        )
        self._lib.XFlush(self._dpy)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            while self._lib.XPending(self._dpy):
                self._lib.XNextEvent(self._dpy, event)
                parsed = _XPropertyEvent.from_buffer_copy(event)
                if parsed.type == 28 and parsed.atom == self._clock_atom:
                    return int(parsed.time) & 0xFFFFFFFF
            remaining = deadline - time.monotonic()
            readable, _, _ = select.select(
                [self._lib.XConnectionNumber(self._dpy)], [], [], max(0, remaining)
            )
            if not readable and not self._lib.XPending(self._dpy):
                break
        raise RuntimeError("X server did not answer a time request")

    def user_time(self, window):
        if isinstance(window, str):
            window = int(window, 16)
        if self._user_atom is None:
            self._user_atom = self._lib.XInternAtom(
                self._dpy,
                b"_NET_WM_USER_TIME",
                False,  # noqa: FBT003 - ctypes only_if_exists
            )
        actual = ctypes.c_ulong()
        fmt = ctypes.c_int()
        count = ctypes.c_ulong()
        remaining = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        self._lib.XGetWindowProperty(
            self._dpy,
            window,
            self._user_atom,
            0,
            1,
            0,
            0,
            ctypes.byref(actual),
            ctypes.byref(fmt),
            ctypes.byref(count),
            ctypes.byref(remaining),
            ctypes.byref(data),
        )
        if not data or fmt.value != 32 or not count.value:
            if data:
                self._lib.XFree(data)
            return None
        try:
            value = int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0])
            return value & 0xFFFFFFFF
        finally:
            self._lib.XFree(data)

    def _mapping(self):
        low, high = ctypes.c_int(), ctypes.c_int()
        self._lib.XDisplayKeycodes(self._dpy, ctypes.byref(low), ctypes.byref(high))
        per = ctypes.c_int()
        symbols = self._lib.XGetKeyboardMapping(
            self._dpy, low.value, high.value - low.value + 1, ctypes.byref(per)
        )
        if not symbols:
            raise RuntimeError("browser unavailable")
        return low.value, high.value, per.value, symbols


class BrowserBackend:
    def __init__(
        self,
        language="en-US",
        upstream_port=6901,
        launcher=None,
        start_url="https://www.home-assistant.io",
    ):
        self.browser = commands(validate_options({"language": language}))["chromium"][
            :-1
        ]
        self.upstream_port = upstream_port
        self.launcher = launcher or self.run
        self.start_url = start_url
        self.lifecycle = threading.RLock()
        self.keymap = None

    def _keymap(self):
        if self.keymap is None:
            self.keymap = KeymapSlots(X11Display())
        return self.keymap

    def run(self, argv, *, capture=False, input=None, timeout=10):
        return subprocess.run(
            argv,
            input=input,
            encoding="utf-8",
            check=True,
            timeout=timeout,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def find_window(self):
        windows = self.run(["wmctrl", "-l"], capture=True).stdout.splitlines()
        # Chromium puts the profile path (which may contain spaces) in res_name.
        # Match X11 res_class independently; wmctrl's text columns cannot parse it.
        try:
            matches = self.run(
                ["xdotool", "search", "--class", "^Chromium$"], capture=True
            ).stdout.split()
        except subprocess.CalledProcessError:
            matches = []
        chromium = {int(value) for value in matches}
        candidates = [
            line.split()[0]
            for line in windows
            if line.split() and int(line.split()[0], 16) in chromium
        ]
        if not candidates:
            return None
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
        return window

    def restore_window(self):
        with self.lifecycle:
            window = self.find_window()
            if window is None:
                self.launcher([*self.browser, self.start_url])
                deadline = time.monotonic() + WINDOW_TIMEOUT
                while window is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                    window = self.find_window()
                if window is None:
                    raise RuntimeError("browser unavailable")
            return self.focus_window(window)

    def focus_window(self, window):
        # Activate by an exact class-derived ID, never a page-controlled title.
        self.run(["wmctrl", "-i", "-a", window])
        self.run(["xdotool", "windowactivate", "--sync", window])
        active = self.run(["xdotool", "getactivewindow"], capture=True).stdout.strip()
        if int(active) != int(window, 16):
            raise RuntimeError("browser unavailable")
        return window

    def open_url(self, url, new_tab):
        with self.lifecycle:
            self._open_url(url, new_tab)

    def _open_url(self, url, new_tab):
        if new_tab:
            # The runtime owns every process, including profile-forwarding launchers.
            self.launcher([*self.browser, "--new-tab", url])
            deadline = time.monotonic() + WINDOW_TIMEOUT
            while self.find_window() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if self.find_window() is None:
                raise RuntimeError("browser unavailable")
            self.restore_window()
        else:
            self.restore_window()
            self.run(["xdotool", "key", "--clearmodifiers", "ctrl+l"])
            self.run(["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", url])
            self.run(["xdotool", "key", "--clearmodifiers", "Return"])

    def click(self, x, y, button, click_count):
        with self.lifecycle:
            self.restore_window()
            self.move_pointer(x, y)
            self.run(
                [
                    "xdotool",
                    "click",
                    "--clearmodifiers",
                    "--repeat",
                    str(click_count),
                    {"left": "1", "middle": "2", "right": "3"}[button],
                ]
            )

    def type_text(self, text):
        with self.lifecycle:
            window = self.restore_window()
            keymap = self._keymap()
            for chunk in keymap.chunks(text):
                if keymap.arm(chunk, window):
                    # Shift does not insert text. Its user-time update is proof
                    # the browser has read the keymap written above.
                    before = keymap.display.user_time(window)
                    stamp = keymap.stamp(window)
                    self.run(["xdotool", "key", "--clearmodifiers", "shift"])
                    stamps = [stamp] if before is None else (stamp, before)
                    keymap.wait_for(window, stamps)
                burst = keymap.stamp(window)
                self.run(
                    [
                        "xdotool",
                        "type",
                        "--clearmodifiers",
                        "--delay",
                        "0",
                        "--file",
                        "-",
                    ],
                    input=chunk,
                    timeout=30,
                )
                keymap.note_burst(chunk, burst)

    def press_key(self, key):
        with self.lifecycle:
            self.restore_window()
            self.run(["xdotool", "key", "--clearmodifiers", key])

    def move_pointer(self, x, y):
        width, height = map(
            int,
            self.run(["xdotool", "getdisplaygeometry"], capture=True).stdout.split(),
        )
        if not (0 <= x < width and 0 <= y < height):
            raise RequestError(400, "invalid request")
        # --sync waits for a movement event even when already at the target.
        # The finite xdotool process flushes its X requests before returning.
        self.run(["xdotool", "mousemove", str(x), str(y)])

    def scroll(self, direction, steps, x, y):
        with self.lifecycle:
            self.restore_window()
            if x is not None:
                self.move_pointer(x, y)
            self.run(
                [
                    "xdotool",
                    "click",
                    "--clearmodifiers",
                    "--repeat",
                    str(steps),
                    {"up": "4", "down": "5", "left": "6", "right": "7"}[direction],
                ]
            )

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


class LocalRestoreServer(socketserver.ThreadingUnixStreamServer):
    """Filesystem-authenticated fixed-operation recovery; no shell or URL input."""

    daemon_threads = True

    def __init__(self, path, backend):
        self.backend = backend
        self.lifecycle = getattr(backend, "lifecycle", threading.RLock())
        self.workers = threading.BoundedSemaphore(4)
        super().__init__(path, LocalRestoreHandler)
        os.chmod(path, 0o600)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
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
        pass


class LocalRestoreHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(1)
        if self.rfile.read(1) != b"R":
            return
        if not self.server.lifecycle.acquire(blocking=False):
            return
        try:
            self.server.backend.restore_window()
            self.wfile.write(b"OK")
        except Exception:  # noqa: BLE001 - never leak a restore traceback
            pass
        finally:
            self.server.lifecycle.release()


def restore_local():
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(WINDOW_TIMEOUT + 3)
        client.connect(LOCAL_SOCKET)
        client.sendall(b"R")
        return 0 if client.recv(2) == b"OK" else 1


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
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)

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
            "/click": "POST",
            "/type_text": "POST",
            "/press_key": "POST",
            "/scroll": "POST",
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
            if self.path
            in (
                "/open_url",
                "/restore_window",
                "/click",
                "/type_text",
                "/press_key",
                "/scroll",
            )
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
            status = 200
            body = result if result is not None else {"ok": True}
        except RequestError:
            status, body, kind = 400, {"error": "invalid request"}, "application/json"
        except Exception:  # noqa: BLE001 - never leak request data
            status, body, kind = (
                502,
                {"error": "dependency unavailable"},
                "application/json",
            )
        finally:
            gate.release()
        # Reply only after the gate is free. A client that sends the next
        # mutation as soon as it reads this response must not observe a 429.
        self.reply(status, body, kind)

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


def key_chord(key):
    if not isinstance(key, str):
        raise ValueError
    parts = key.split("+")
    modifiers, name = parts[:-1], parts[-1]
    if (
        len(modifiers) > 3
        or len(set(modifiers)) != len(modifiers)
        or any(mod not in ("Ctrl", "Alt", "Shift") for mod in modifiers)
    ):
        raise ValueError
    names = {
        "Enter": "Return",
        "Escape": "Escape",
        "Tab": "Tab",
        "Space": "space",
        "Backspace": "BackSpace",
        "Delete": "Delete",
        "Home": "Home",
        "End": "End",
        "PageUp": "Prior",
        "PageDown": "Next",
        "Left": "Left",
        "Right": "Right",
        "Up": "Up",
        "Down": "Down",
        **{f"F{n}": f"F{n}" for n in range(1, 13)},
        **{
            c: c.lower()
            for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        },
    }
    if name not in names:
        raise ValueError
    # Uppercase X keysyms imply Shift; only an explicit Shift modifier should do so.
    return "+".join([mod.lower() for mod in modifiers] + [names[name]])


def validate_request(path, data):
    fields = {
        "/open_url": {"url", "new_tab"},
        "/click": {"x", "y", "button", "click_count"},
        "/type_text": {"text"},
        "/press_key": {"key"},
        "/scroll": {"direction", "steps", "x", "y"},
        "/restore_window": set(),
        "/wait": {"idle_ms", "timeout_ms", "region"},
        "/record": {"seconds"},
        "/screenshot": set(),
    }
    if not isinstance(data, dict) or data.keys() - fields[path]:
        raise ValueError
    if path == "/scroll":
        direction = data.get("direction")
        if direction not in ("up", "down", "left", "right") or ("x" in data) != (
            "y" in data
        ):
            raise ValueError
        x, y = None, None
        if "x" in data:
            x = integer(data["x"], 0, 16383)
            y = integer(data["y"], 0, 16383)
        return direction, integer(data.get("steps", 1), 1, 20), x, y
    if path == "/press_key":
        return (key_chord(data.get("key")),)
    if path == "/type_text":
        text = data.get("text")
        if (
            not isinstance(text, str)
            or not 1 <= len(text) <= 1024
            or any(
                ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
                for c in text
            )
        ):
            raise ValueError
        return (text,)
    if path == "/click":
        x = integer(data.get("x"), 0, 16383)
        y = integer(data.get("y"), 0, 16383)
        button = data.get("button", "left")
        if button not in ("left", "right", "middle"):
            raise ValueError
        return x, y, button, integer(data.get("click_count", 1), 1, 2)
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
    """The desktop menu's fixed-operation client; runtime owns the API server."""
    if (sys.argv[1:] if argv is None else argv) != ["--restore-local"]:
        return 1
    try:
        return restore_local()
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
