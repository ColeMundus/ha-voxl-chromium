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


def test_opt_in_token_is_stable_private_and_never_follows_links(tmp_path):
    import stat

    mod = runtime()
    disabled = mod.validate_options({})
    assert disabled["automation_enabled"] is False
    assert mod.resolve_automation_token(disabled, tmp_path) == ""
    assert not (tmp_path / "automation-token").exists()
    options = mod.validate_options({"automation_enabled": True})
    first = mod.resolve_automation_token(options, tmp_path)
    assert len(first) >= 43
    assert mod.resolve_automation_token(options, tmp_path) == first
    assert stat.S_IMODE((tmp_path / "automation-token").stat().st_mode) == 0o600
    other = tmp_path / "other"
    other.mkdir()
    assert mod.resolve_automation_token(options, other) != first
    manual = mod.validate_options({"automation_token": "m" * 32})
    assert mod.resolve_automation_token(manual, tmp_path) == "m" * 32
    (tmp_path / "automation-token").unlink()
    (tmp_path / "automation-token").symlink_to(other / "automation-token")
    with pytest.raises((ValueError, OSError)):
        mod.resolve_automation_token(options, tmp_path)
    with pytest.raises(ValueError):
        mod.validate_options({"automation_enabled": "true"})


def test_monitor_does_not_treat_closed_browser_as_critical(monkeypatch):
    from types import SimpleNamespace

    mod = runtime()
    closed = SimpleNamespace(poll=lambda: 0, returncode=0)
    alive = SimpleNamespace(poll=lambda: None)
    stopped = []
    monkeypatch.setattr(mod, "stop_all", lambda processes: stopped.append(processes))

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        mod.monitor({"chromium": closed, "voxl": alive})
    assert stopped


def test_browser_manager_reaps_and_never_relaunches_after_stop(tmp_path):
    import sys
    import threading

    mod = runtime()
    manager = mod.BrowserManager()
    saved = tmp_path / "profile"
    try:
        first = manager.launch([sys.executable, "-c", "pass"])
        assert first.wait(timeout=3) == 0
        live = manager.launch(
            [
                sys.executable,
                "-c",
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_text('persistent'); time.sleep(60)",
                str(saved),
            ]
        )
        assert len(manager.processes) == 1
        assert list(manager.processes.values()) == [live]
    finally:
        manager.stop()
    assert live.poll() is not None
    with pytest.raises(RuntimeError, match="stopping"):
        manager.launch([sys.executable, "-c", "pass"])
    assert all(child.poll() is not None for child in manager.processes.values())
    # A stopped event returns without restarting or stopping unrelated services.
    stopped = threading.Event()
    stopped.set()
    assert mod.monitor({}, stopped=stopped, cleanup=False) == 0
    with pytest.raises(SystemExit):
        mod.wait_ready(lambda: False, {}, stopped=stopped)


def test_discovery_helper_never_inherits_credentials_and_is_optional(monkeypatch):
    from types import SimpleNamespace

    mod = runtime()
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-private")
    monkeypatch.setenv("HASSIO_TOKEN", "legacy-private")
    monkeypatch.setenv("AUTOMATION_TOKEN", "automation-private")
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        assert "private" not in repr((argv, kwargs))
        assert kwargs["env"] == {"PATH": "/usr/local/bin:/usr/bin:/bin"}
        assert kwargs["stdin"] == mod.subprocess.PIPE
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    assert mod.start_discovery().pid == 42
    assert calls

    def fail(*args, **kwargs):
        raise OSError("not available")

    monkeypatch.setattr(mod.subprocess, "Popen", fail)
    assert mod.start_discovery() is None


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


def test_automation_token_default_validation_and_browser_exclusion():
    import os

    mod = runtime()
    assert mod.validate_options({})["automation_token"] == ""
    for token in (None, 123, "x" * 31, "x" * 32 + " ", "x" * 32 + "\n", "é" * 32):
        with pytest.raises(ValueError):
            mod.validate_options({"automation_token": token})
    token = "Ab~!_-/+.=" * 4
    options = mod.validate_options({"automation_token": token})
    assert options["automation_token"] == token
    assert token not in repr(mod.commands(options))
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
