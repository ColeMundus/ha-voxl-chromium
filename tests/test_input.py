"""Native input contract through the private HTTP boundary."""

from types import SimpleNamespace

import control
import pytest
from test_control import request, serving


def native_backend(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = {
            ("wmctrl", "-l"): "0x0123 0 host Browser\n",
            ("xdotool", "search", "--class", "^Chromium$"): "291\n",
            ("xdotool", "getactivewindow"): "291\n",
            ("xdotool", "getdisplaygeometry"): "1280 720\n",
        }.get(tuple(argv), "")
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(control.subprocess, "run", run)
    return control.BrowserBackend(), calls


def test_click_vertical_slice_restores_checks_geometry_and_clicks(monkeypatch):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        assert request(server, "POST", "/click", {"x": 1279, "y": 719}) == (
            200,
            "application/json",
            b'{"ok":true}',
        )
    argv = [args for args, _ in calls]
    assert ["wmctrl", "-i", "-a", "0x0123"] in argv
    assert ["xdotool", "windowactivate", "--sync", "0x0123"] in argv
    assert ["xdotool", "getdisplaygeometry"] in argv
    assert argv[-2:] == [
        ["xdotool", "mousemove", "1279", "719"],
        ["xdotool", "click", "--clearmodifiers", "--repeat", "1", "1"],
    ]
    assert all(
        k["check"] and k["timeout"] == 10 and not k.get("shell") for _, k in calls
    )


@pytest.mark.parametrize(
    "button,number", [("left", "1"), ("middle", "2"), ("right", "3")]
)
def test_click_button_and_double_click(monkeypatch, button, number):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        assert (
            request(
                server,
                "POST",
                "/click",
                {"x": 0, "y": 0, "button": button, "click_count": 2},
            )[0]
            == 200
        )
    assert calls[-1][0] == [
        "xdotool",
        "click",
        "--clearmodifiers",
        "--repeat",
        "2",
        number,
    ]


def test_click_rejects_invalid_schema_and_runtime_points_without_input(monkeypatch):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        for data in [
            {},
            {"x": 0},
            {"y": 0},
            {"x": True, "y": 0},
            {"x": 0.0, "y": 0},
            {"x": -1, "y": 0},
            {"x": 16384, "y": 0},
            {"x": 0, "y": "0"},
            {"x": 0, "y": 0, "button": []},
            {"x": 0, "y": 0, "button": "LEFT"},
            *[{"x": 0, "y": 0, "click_count": n} for n in [True, 1.0, 0, 3]],
            {"x": 0, "y": 0, "extra": 1},
        ]:
            assert request(server, "POST", "/click", data)[0] == 400
        assert calls == []
        for x, y in [(1280, 0), (0, 720), (16383, 16383)]:
            assert request(server, "POST", "/click", {"x": x, "y": y}) == (
                400,
                "application/json",
                b'{"error":"invalid request"}',
            )
    assert not any(
        args[1] in ("mousemove", "click") for args, _ in calls if args[0] == "xdotool"
    )


def test_type_text_literal_unicode_stdin_only_and_private_failure(monkeypatch, capsys):
    backend, calls = native_backend(monkeypatch)
    text = "--secret $(touch /bad); 'é漢字🙂' " + "é" * 990
    with serving(backend) as server:
        assert request(server, "POST", "/type_text", {"text": text}) == (
            200,
            "application/json",
            b'{"ok":true}',
        )
        assert calls[-1][0] == [
            "xdotool",
            "type",
            "--clearmodifiers",
            "--delay",
            "10",
            "--file",
            "-",
        ]
        assert calls[-1][1]["input"] == text
        assert calls[-1][1]["encoding"] == "utf-8"
        assert calls[-1][1]["timeout"] == 30
        assert not any(text in str(args) for args, _ in calls)
        assert ["xdotool", "windowactivate", "--sync", "0x0123"] in [
            args for args, _ in calls
        ]

        def fail(*args, **kwargs):
            raise RuntimeError(text)

        monkeypatch.setattr(backend, "run", fail)
        assert request(server, "POST", "/type_text", {"text": text}) == (
            502,
            "application/json",
            b'{"error":"dependency unavailable"}',
        )
    assert capsys.readouterr() == ("", "")


def test_type_text_rejects_controls_surrogates_and_bad_schema(monkeypatch):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        for data in [
            {},
            {"text": ""},
            {"text": None},
            {"text": 12},
            {"text": []},
            {"text": "a" * 1025},
            {"text": "x", "extra": True},
            *[
                {"text": "a" + chr(n) + "b"}
                for n in [*range(32), *range(127, 160), 0xD800, 0xDFFF]
            ],
        ]:
            assert request(server, "POST", "/type_text", data)[0] == 400
        assert calls == []
        assert request(server, "POST", "/type_text", {"text": "🙂" * 1024})[0] == 200


def test_press_key_allowlist_translation_and_case(monkeypatch):
    backend, calls = native_backend(monkeypatch)
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
        "Ctrl+A": "ctrl+a",
        "Ctrl+Alt+Shift+Z": "ctrl+alt+shift+z",
        "Shift+Ctrl+PageDown": "shift+ctrl+Next",
    }
    with serving(backend) as server:
        for key, mapped in names.items():
            calls.clear()
            assert request(server, "POST", "/press_key", {"key": key})[0] == 200
            assert calls[-1][0] == ["xdotool", "key", "--clearmodifiers", mapped]
            assert ["xdotool", "windowactivate", "--sync", "0x0123"] in [
                a for a, _ in calls
            ]


def test_press_key_rejects_arbitrary_keysyms_operations_and_schema(monkeypatch):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        for data in [
            {},
            {"key": None},
            {"key": []},
            {"key": True},
            {"key": ""},
            {"key": "A", "extra": 1},
            *[
                {"key": k}
                for k in [
                    "ctrl+a",
                    "CTRL+A",
                    "Control+A",
                    "Super+A",
                    "Meta+A",
                    "Ctrl+Ctrl+A",
                    "Ctrl+Alt+Shift+Ctrl+A",
                    "Ctrl",
                    "A+B",
                    "Ctrl+",
                    "+A",
                    "Ctrl++A",
                    "Return",
                    "space",
                    "BackSpace",
                    "Prior",
                    "Next",
                    "F0",
                    "F13",
                    "XF86PowerOff",
                    "keydown A",
                    "--window 123",
                    "A B",
                    "é",
                    "AA",
                    "Ctrl+é",
                ]
            ],
        ]:
            assert request(server, "POST", "/press_key", data)[0] == 400
    assert calls == []


@pytest.mark.parametrize(
    "direction,button", [("up", "4"), ("down", "5"), ("left", "6"), ("right", "7")]
)
def test_scroll_default_pointer_or_validated_point(monkeypatch, direction, button):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        assert request(server, "POST", "/scroll", {"direction": direction})[0] == 200
        assert calls[-1][0] == [
            "xdotool",
            "click",
            "--clearmodifiers",
            "--repeat",
            "1",
            button,
        ]
        assert not any(a[1] == "mousemove" for a, _ in calls if a[0] == "xdotool")
        assert ["xdotool", "windowactivate", "--sync", "0x0123"] in [
            a for a, _ in calls
        ]
        calls.clear()
        assert (
            request(
                server,
                "POST",
                "/scroll",
                {"direction": direction, "steps": 20, "x": 1279, "y": 719},
            )[0]
            == 200
        )
        assert calls[-2][0] == ["xdotool", "mousemove", "1279", "719"]
        assert calls[-1][0] == [
            "xdotool",
            "click",
            "--clearmodifiers",
            "--repeat",
            "20",
            button,
        ]


def test_scroll_rejects_invalid_schema_and_runtime_bounds(monkeypatch):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        for data in [
            {},
            {"direction": "UP"},
            {"direction": []},
            {"direction": None},
            *[{"direction": "up", "steps": n} for n in [True, 1.0, 0, 21, "1"]],
            {"direction": "up", "x": 0},
            {"direction": "up", "y": 0},
            {"direction": "up", "x": None, "y": None},
            *[{"direction": "up", "x": n, "y": 0} for n in [True, 1.0, -1, 16384]],
            {"direction": "up", "extra": 1},
        ]:
            assert request(server, "POST", "/scroll", data)[0] == 400
        assert calls == []
        for x, y in [(1280, 0), (0, 720)]:
            assert (
                request(server, "POST", "/scroll", {"direction": "up", "x": x, "y": y})[
                    0
                ]
                == 400
            )
    assert not any(
        a[1] in ("mousemove", "click") for a, _ in calls if a[0] == "xdotool"
    )


INPUT_REQUESTS = [
    ("/click", {"x": 0, "y": 0}),
    ("/type_text", {"text": "private input"}),
    ("/press_key", {"key": "Ctrl+A"}),
    ("/scroll", {"direction": "down"}),
]


def test_input_auth_origin_methods_and_bounded_strict_json(monkeypatch, capsys):
    backend, calls = native_backend(monkeypatch)
    with serving(backend) as server:
        for path, data in INPUT_REQUESTS:
            for headers, status in [
                ({"Authorization": ""}, 401),
                ({"Authorization": "Bearer wrong"}, 401),
                ({"Origin": "null"}, 403),
                ({"Origin": ""}, 403),
            ]:
                assert request(server, "POST", path, data, headers)[0] == status
            assert request(server, "GET", path)[0] == 405
            assert (
                request(
                    server,
                    "POST",
                    path,
                    "x" * 16385,
                    {"Content-Type": "application/json"},
                )[0]
                == 413
            )
            for raw in ["[]", "null", '{"x":0,"x":1}', '{"x":NaN}']:
                assert (
                    request(
                        server, "POST", path, raw, {"Content-Type": "application/json"}
                    )[0]
                    == 400
                )
    assert calls == []
    assert capsys.readouterr() == ("", "")


def test_every_input_shares_mutation_gate_with_open_and_restore(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    backend, calls = native_backend(monkeypatch)
    mutations = INPUT_REQUESTS + [
        ("/open_url", {"url": "https://example.com"}),
        ("/restore_window", {}),
    ]
    with serving(backend) as server, ThreadPoolExecutor(max_workers=1) as pool:
        for blocked_path, blocked_data in mutations:
            entered, release = threading.Event(), threading.Event()

            def block(*args, entered=entered, release=release):
                entered.set()
                assert release.wait(3)

            with monkeypatch.context() as patcher:
                patcher.setattr(backend, blocked_path[1:], block)
                pending = pool.submit(
                    request, server, "POST", blocked_path, blocked_data
                )
                assert entered.wait(1)
                try:
                    for path, data in mutations:
                        assert request(server, "POST", path, data)[0] == 429
                    assert request(server)[0] == 200
                    assert (
                        request(server, "POST", "/click", {"x": True, "y": 0})[0] == 400
                    )
                finally:
                    release.set()
                assert pending.result()[0] == 200
        assert request(server, "POST", "/click", {"x": 0, "y": 0})[0] == 200


@pytest.mark.parametrize("path,data", INPUT_REQUESTS)
def test_input_operations_wait_for_shared_lifecycle_lock(monkeypatch, path, data):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    backend, calls = native_backend(monkeypatch)
    started = threading.Event()

    def operate():
        started.set()
        getattr(backend, path[1:])(*control.validate_request(path, data))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with backend.lifecycle:
            pending = pool.submit(operate)
            assert started.wait(1)
            assert not pending.done()
            assert calls == []
        pending.result(timeout=2)
    assert calls
