import importlib.util
from pathlib import Path

import pytest

RUNTIME = Path(__file__).parents[1] / "voxl_chromium" / "runtime.py"


def runtime():
    assert RUNTIME.exists(), "Add-on runtime has not been implemented"
    spec = importlib.util.spec_from_file_location("runtime", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_options_produce_private_persistent_browser():
    mod = runtime()
    options = mod.validate_options({})
    commands = mod.commands(options)
    assert "--user-data-dir=/data/profile" in commands["chromium"]
    assert "--remote-debugging-port" not in " ".join(commands["chromium"])
    assert "--addr=127.0.0.1" in commands["voxl"]
    assert "--enable-basic-auth=false" in commands["voxl"]
    assert "-nolisten" in commands["xvfb"]
    assert "-shmem" in commands["xvfb"]
    assert "--files-root=/data/Downloads" in commands["voxl"]
    assert commands["nginx"][0] == "/usr/sbin/nginx"
    assert options["start_url"] == "https://www.home-assistant.io"


@pytest.mark.parametrize(
    "options",
    [
        {"start_url": "--no-sandbox"},
        {"start_url": "javascript:alert(1)"},
        {"start_url": "file:///etc/passwd"},
        {"start_url": "https://user:pass@example.com"},
        {"width": 100},
        {"height": 5000},
        {"fps": 0},
        {"fps": True},
        {"bitrate_kbps": 499},
        {"resize_scale": float("nan")},
        {"audio": "false"},
        {"timezone": "../etc/passwd"},
        {"language": "--bad-flag"},
        {"unknown": "value"},
    ],
)
def test_reject_invalid_options(options):
    with pytest.raises(ValueError):
        runtime().validate_options(options)


def test_arguments_are_not_shell_interpreted():
    mod = runtime()
    url = "https://example.com/?q=$(touch%20/bad);echo"
    options = mod.validate_options({"start_url": url})
    assert mod.commands(options)["chromium"][-1] == url


def test_automation_token_default_validation_and_private_launch(monkeypatch, tmp_path):
    import os
    import stat
    from types import SimpleNamespace

    mod = runtime()
    assert mod.validate_options({})["automation_token"] == ""
    for token in (None, 123, "x" * 31, "x" * 32 + " ", "x" * 32 + "\n", "é" * 32):
        with pytest.raises(ValueError):
            mod.validate_options({"automation_token": token})
    token = "Ab~!_-/+.=" * 4
    options = mod.validate_options({"automation_token": token})
    assert options["automation_token"] == token
    assert token not in repr(mod.commands(options))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    def popen(argv, **kwargs):
        assert token not in repr(argv) and "env" not in kwargs
        path = Path(argv[argv.index("--token-file") + 1])
        assert path.parent == tmp_path
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == token
        assert kwargs == {"start_new_session": True}
        path.unlink()
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    assert mod.start_control(options).pid == 42
    assert not list(tmp_path.iterdir())
    assert mod.start_control(mod.validate_options({})) is None
    assert os.environ.get("automation_token") is None


def test_child_failure_terminates_other_processes():
    import subprocess
    import sys
    import time

    mod = runtime()
    sibling = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    failed = subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(7)"], start_new_session=True
    )
    try:
        started = time.monotonic()
        assert mod.monitor({"sibling": sibling, "failed": failed}) == 1
        assert time.monotonic() - started < 5
        assert sibling.poll() is not None
    finally:
        if sibling.poll() is None:
            sibling.kill()
        sibling.wait()
        failed.wait()


def test_browser_can_flush_profile_before_display_exits(tmp_path):
    import subprocess
    import sys
    import time

    display_marker = tmp_path / "display"
    saved = tmp_path / "saved"
    ready = tmp_path / "ready"
    display_script = """
import pathlib,signal,sys,time
p=pathlib.Path(sys.argv[1]); p.touch()
def stop(*_):
    p.unlink(); raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while True: time.sleep(.01)
"""
    browser_script = """
import pathlib,signal,sys,time
p,s,r=map(pathlib.Path,sys.argv[1:])
def stop(*_):
    time.sleep(.15)
    s.write_text(str(p.exists())); raise SystemExit(0)
signal.signal(signal.SIGTERM,stop); r.touch()
while True: time.sleep(.01)
"""
    display = subprocess.Popen(
        [sys.executable, "-c", display_script, str(display_marker)],
        start_new_session=True,
    )
    browser = subprocess.Popen(
        [
            sys.executable,
            "-c",
            browser_script,
            str(display_marker),
            str(saved),
            str(ready),
        ],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not (ready.exists() and display_marker.exists()):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        runtime().stop_all({"xvfb": display, "chromium": browser})
        assert saved.read_text() == "True"
    finally:
        for proc in (display, browser):
            if proc.poll() is None:
                proc.kill()
            proc.wait()
