"""Host controller tests: no real GitHub calls or systemd mutations."""

import importlib.util
import json
import subprocess

import pytest


def load_controller():
    spec = importlib.util.find_spec("runner.controller")
    assert spec is not None, "runner.controller has not been implemented"
    from runner import controller

    return controller


def run_record(**changes):
    run = {
        "id": 12,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "head_branch": "main",
        "head_repository": {"full_name": "ColeMundus/ha-voxl-chromium"},
        "event": "push",
    }
    run.update(changes)
    return run


class Process:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        value = next(self.payloads)
        if isinstance(value, Exception):
            raise value
        # gh --paginate on Ubuntu 24.04 emits consecutive JSON documents.
        output = (
            "\n".join(json.dumps(page) for page in value)
            if isinstance(value, list)
            else json.dumps(value)
        )
        return subprocess.CompletedProcess(args, 0, output, "")


def pages(key, entries):
    return [{"total_count": len(entries), key: entries}]


def job(**changes):
    result = {"status": "queued", "labels": ["self-hosted", "mu-voxl-amd64"]}
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "changes,accepted",
    [
        ({}, True),
        ({"event": "workflow_dispatch"}, True),
        ({"event": "schedule"}, True),
        ({"event": "pull_request"}, False),
        ({"event": "pull_request_target"}, False),
        ({"event": "workflow_run"}, False),
        ({"head_branch": "automation/update-upstream"}, False),
        ({"head_repository": {"full_name": "attacker/ha-voxl-chromium"}}, False),
        ({"head_repository": None}, False),
        ({"path": ".github/workflows/evil.yml"}, False),
        ({"path": ".github/workflows/ci.yml@refs/heads/main"}, True),
        ({"path": ".github/workflows/ci.yml@evil"}, False),
        ({"path": ".github/workflows/ci.yml/../evil"}, False),
        ({"name": "Other CI"}, False),
    ],
)
def test_only_trusted_workflow_jobs_are_queried(changes, accepted):
    c = load_controller()
    responses = [pages("workflow_runs", [run_record(**changes)])]
    if accepted:
        responses.append(pages("jobs", [job()]))
    responses += [pages("workflow_runs", []), pages("runners", [])]
    process = Process(responses)
    snapshot = c.GitHub(process=process).snapshot()
    assert snapshot.queued is accepted
    assert snapshot.in_progress is False
    job_requests = [args for args, _ in process.calls if "/jobs?" in args[-1]]
    assert bool(job_requests) is accepted
    for args, options in process.calls:
        assert args[:3] == ["gh", "api", "--paginate"]
        assert "--slurp" not in args
        assert options["timeout"] > 0
        assert options["shell"] is False
        assert options["capture_output"] is True


@pytest.mark.parametrize(
    "changes,queued,running",
    [
        ({}, True, False),
        ({"status": "in_progress"}, False, True),
        ({"status": "completed"}, False, False),
        ({"labels": ["mu-voxl-amd64-extra"]}, False, False),
        ({"labels": ["MU-VOXL-AMD64"]}, False, False),
        ({"labels": ["ubuntu-24.04-arm"]}, False, False),
        ({"name": "$(touch /tmp/not-executed)"}, True, False),
    ],
)
def test_actual_job_labels_and_status_drive_demand(changes, queued, running):
    c = load_controller()
    process = Process(
        [
            pages("workflow_runs", []),
            pages("workflow_runs", [run_record()]),
            pages("jobs", [job(**changes)]),
            pages("runners", []),
        ]
    )
    snapshot = c.GitHub(process=process).snapshot()
    assert (snapshot.queued, snapshot.in_progress) == (queued, running)


def runner(**changes):
    result = {
        "labels": [{"name": "mu-voxl-amd64"}],
        "busy": False,
        "status": "online",
    }
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "runners,busy",
    [
        ([], None),
        ([runner()], False),
        ([runner(busy=True)], True),
        ([runner(status="offline")], None),
        ([runner(busy=True, status="offline")], True),
        ([runner(labels=[{"name": "other"}], busy=True)], None),
        ([runner(), runner(busy=True)], True),
        ([runner(), runner(status="offline")], None),
    ],
)
def test_runner_confirmation_requires_matching_online_nonbusy_runner(runners, busy):
    c = load_controller()
    process = Process(
        [
            pages("workflow_runs", []),
            pages("workflow_runs", []),
            pages("runners", runners),
        ]
    )
    assert c.GitHub(process=process).snapshot().busy is busy


@pytest.mark.parametrize(
    "response",
    [
        subprocess.TimeoutExpired("gh", 30, output="SECRET"),
        subprocess.CalledProcessError(1, "gh", stderr="SECRET rate limit"),
        FileNotFoundError("SECRET"),
        {},
        [],
        [{"workflow_runs": []}],
        [{"total_count": 2, "workflow_runs": []}],
        [{"total_count": 0, "workflow_runs": None}],
        [{"total_count": 1, "workflow_runs": [None]}],
    ],
)
def test_api_failures_are_sanitized_and_not_empty_snapshots(response):
    c = load_controller()
    with pytest.raises(c.ControllerError) as exc:
        c.GitHub(process=Process([response])).snapshot()
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("bad_id", ["12/../../evil", "12;poweroff", True, -1, None])
def test_run_id_cannot_change_fixed_jobs_api_path(bad_id):
    c = load_controller()
    process = Process([pages("workflow_runs", [run_record(id=bad_id)])])
    with pytest.raises(c.ControllerError):
        c.GitHub(process=process).snapshot()
    assert len(process.calls) == 1


def test_pagination_includes_jobs_on_later_pages():
    c = load_controller()
    process = Process(
        [
            pages("workflow_runs", [run_record()]),
            [
                {"total_count": 2, "jobs": [job(labels=["other"])]},
                {"total_count": 2, "jobs": [job()]},
            ],
            pages("workflow_runs", []),
            pages("runners", [runner()]),
        ]
    )
    snapshot = c.GitHub(process=process).snapshot()
    assert snapshot.queued and snapshot.busy is False


class FakeAPI:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        result = next(self.snapshots)
        if isinstance(result, Exception):
            raise result
        return result


class FakeVM:
    def __init__(self, status="on"):
        self.value = status
        self.actions = []

    def status(self):
        return self.value

    def start(self):
        self.actions.append("start")
        self.value = "starting"

    def stop(self):
        self.actions.append("stop")
        self.value = "off"


def controller_for(snapshots, vm_status="on", **state):
    c = load_controller()
    api = FakeAPI(snapshots)
    vm = FakeVM(vm_status)
    instance = c.Controller(api, vm, c.State(**state))
    return instance, api, vm


def test_trusted_queue_starts_off_vm_only_once():
    c = load_controller()
    ctrl, api, vm = controller_for([c.Snapshot(queued=True)] * 2, "off")
    ctrl.poll(1000)
    ctrl.poll(1020)
    assert vm.actions == ["start"]
    assert ctrl.state.phase == "starting"
    assert ctrl.state.started_at == 1000
    assert ctrl.state.idle_since is None
    assert api.calls == 2


@pytest.mark.parametrize("snapshot", ["idle", "running", "busy"])
def test_off_vm_never_starts_without_actual_queue(snapshot):
    c = load_controller()
    snapshots = {
        "idle": c.Snapshot(busy=False),
        "running": c.Snapshot(in_progress=True),
        "busy": c.Snapshot(busy=True),
    }
    ctrl, _, vm = controller_for([snapshots[snapshot]], "off")
    ctrl.poll(1000)
    assert vm.actions == []
    assert ctrl.state.phase == "off"


def test_postjob_idle_waits_full_sixty_seconds_and_requeries_before_stop():
    c = load_controller()
    ctrl, api, vm = controller_for(
        [c.Snapshot(in_progress=True, busy=True)] + [c.Snapshot(busy=False)] * 4,
        phase="on",
        started_at=100,
        last_seen=990,
    )
    ctrl.poll(1000)
    ctrl.poll(1020)
    ctrl.poll(1079)
    assert vm.actions == []
    ctrl.poll(1080)
    assert vm.actions == ["stop"]
    assert api.calls == 5
    assert ctrl.state.phase == "off"


@pytest.mark.parametrize("activity", ["queued", "running", "busy", "unknown"])
def test_activity_or_uncertainty_clears_idle_timer(activity):
    c = load_controller()
    snapshots = {
        "queued": c.Snapshot(queued=True, busy=False),
        "running": c.Snapshot(in_progress=True, busy=False),
        "busy": c.Snapshot(busy=True),
        "unknown": c.Snapshot(),
    }
    ctrl, _, vm = controller_for(
        [snapshots[activity]],
        phase="on",
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    ctrl.poll(1000)
    assert vm.actions == []
    assert ctrl.state.idle_since is None


def test_startup_grace_protects_idle_vm_for_six_hundred_seconds():
    c = load_controller()
    ctrl, _, vm = controller_for(
        [c.Snapshot(busy=False)] * 4,
        phase="starting",
        started_at=1000,
        last_seen=1590,
    )
    ctrl.poll(1599)
    assert vm.actions == []
    ctrl.poll(1600)
    assert ctrl.state.phase == "on"
    assert vm.actions == []
    ctrl.poll(1660)
    assert vm.actions == ["stop"]


def test_actual_job_ends_bootstrap_and_then_uses_postjob_idle():
    c = load_controller()
    ctrl, _, vm = controller_for(
        [
            c.Snapshot(in_progress=True),
            c.Snapshot(busy=False),
            c.Snapshot(busy=False),
            c.Snapshot(busy=False),
        ],
        phase="starting",
        started_at=1000,
        last_seen=1000,
    )
    ctrl.poll(1020)
    assert ctrl.state.phase == "on"
    ctrl.poll(1040)
    ctrl.poll(1100)
    assert vm.actions == ["stop"]


@pytest.mark.parametrize("phase", ["starting", "on"])
def test_api_failure_preserves_boot_time_but_invalidates_idle(phase):
    c = load_controller()
    ctrl, _, vm = controller_for(
        [c.ControllerError("API unavailable")],
        phase=phase,
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    assert ctrl.poll(1000) is False
    assert vm.actions == []
    assert ctrl.state.started_at == 100
    assert ctrl.state.idle_since is None


@pytest.mark.parametrize("race", ["queued", "running", "busy", "unknown", "error"])
def test_final_shutdown_recheck_aborts_on_new_work_or_uncertainty(race):
    c = load_controller()
    snapshots = {
        "queued": c.Snapshot(queued=True, busy=False),
        "running": c.Snapshot(in_progress=True, busy=False),
        "busy": c.Snapshot(busy=True),
        "unknown": c.Snapshot(),
        "error": c.ControllerError("API unavailable"),
    }
    ctrl, api, vm = controller_for(
        [c.Snapshot(busy=False), snapshots[race]],
        phase="on",
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    ctrl.poll(1000)
    assert vm.actions == []
    assert api.calls == 2
    assert ctrl.state.idle_since is None


def test_unknown_vm_status_never_mutates_service():
    ctrl, api, vm = controller_for([], "unknown")
    assert ctrl.poll(1000) is False
    assert api.calls == 0
    assert vm.actions == []


@pytest.mark.parametrize("now", [800, 1060, 100000])
def test_wall_clock_reset_or_long_observation_gap_restarts_grace(now):
    c = load_controller()
    ctrl, _, vm = controller_for(
        [c.Snapshot(busy=False)],
        phase="on",
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    ctrl.poll(now)
    assert vm.actions == []
    assert ctrl.state.started_at == now
    assert ctrl.state.idle_since is None
    assert ctrl.state.phase == "starting"


def test_adopting_vm_with_missing_state_gives_fresh_startup_grace():
    c = load_controller()
    ctrl, _, vm = controller_for([c.Snapshot(busy=False)])
    ctrl.poll(1000)
    assert ctrl.state.phase == "starting"
    assert ctrl.state.started_at == 1000
    assert vm.actions == []


@pytest.mark.parametrize(
    "code,detail,expected",
    [
        (0, None, "on"),
        (3, "inactive", "off"),
        (3, "failed", "off"),
        (3, "activating", "starting"),
        (3, "deactivating", "unknown"),
        (4, None, "unknown"),
        (1, None, "unknown"),
    ],
)
def test_systemd_state_is_read_only_and_distinguishes_transitions(
    code, detail, expected
):
    c = load_controller()
    calls = []

    def process(args, **kwargs):
        calls.append((args, kwargs))
        if "is-active" in args:
            return subprocess.CompletedProcess(args, code, "", "SECRET")
        return subprocess.CompletedProcess(args, 0, detail + "\n", "")

    assert c.SystemdVM(process=process).status() == expected
    assert calls[0][0] == [
        "/usr/bin/systemctl",
        "is-active",
        "--quiet",
        "gh-voxl-vm.service",
    ]
    assert all("sudo" not in args for args, _ in calls)
    assert all(options["timeout"] > 0 for _, options in calls)


@pytest.mark.parametrize("action", ["start", "stop"])
def test_only_fixed_sudo_systemctl_actions_are_executed(action):
    c = load_controller()
    process = Process([{}])
    getattr(c.SystemdVM(process=process), action)()
    args, options = process.calls[0]
    assert args == ["sudo", "-n", "/usr/bin/systemctl", action, "gh-voxl-vm.service"]
    assert options["shell"] is False
    assert options["timeout"] > 0
    assert options["check"] is True


@pytest.mark.parametrize("method", ["status", "start", "stop"])
def test_systemd_process_errors_never_expose_output(method):
    c = load_controller()
    process = Process([subprocess.TimeoutExpired("SECRET", 1, output="SECRET")])
    with pytest.raises(c.ControllerError) as exc:
        getattr(c.SystemdVM(process=process), method)()
    assert "SECRET" not in str(exc.value)


def test_service_action_failure_is_failsafe_and_logs_only_once(caplog):
    c = load_controller()
    ctrl, _, vm = controller_for([c.Snapshot(queued=True)] * 2, "off")

    def fail():
        raise c.ControllerError("VM operation failed")

    vm.start = fail
    assert ctrl.poll(1000) is False
    assert ctrl.poll(1020) is False
    assert vm.actions == []
    assert len([r for r in caplog.records if "failsafe" in r.message]) == 1


def test_steady_polls_are_not_logged(caplog):
    import logging

    c = load_controller()
    ctrl, _, _ = controller_for(
        [c.Snapshot(busy=True)] * 3, phase="on", started_at=100, last_seen=990
    )
    with caplog.at_level(logging.INFO):
        for now in (1000, 1020, 1040):
            assert ctrl.poll(now)
    assert caplog.records == []


def test_state_roundtrip_is_atomic_and_private(tmp_path, monkeypatch):
    c = load_controller()
    path = tmp_path / "state.json"
    original = c.State(phase="on", started_at=100, idle_since=900, last_seen=990)
    c.save_state(path, original)
    assert c.load_state(path) == original
    assert path.stat().st_mode & 0o777 == 0o600
    original_bytes = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("SECRET disk error")

    monkeypatch.setattr(c.os, "replace", fail)
    with pytest.raises(c.ControllerError):
        c.save_state(path, c.State())
    assert path.read_bytes() == original_bytes
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "data",
    [
        "bad json",
        "null",
        "[]",
        '{"phase":"evil"}',
        '{"phase":"on","started_at":NaN}',
        '{"phase":"on","started_at":-1}',
        '{"phase":"on","started_at":true}',
        '{"phase":"on","started_at":100,"last_seen":90}',
        '{"phase":"on","idle_since":100,"last_seen":90}',
        '{"unexpected":"value"}',
    ],
)
def test_corrupt_state_renews_grace_instead_of_using_timestamps(tmp_path, data):
    c = load_controller()
    path = tmp_path / "state.json"
    path.write_text(data)
    path.chmod(0o600)
    assert c.load_state(path) == c.State()


def test_missing_state_is_safe_default(tmp_path):
    c = load_controller()
    assert c.load_state(tmp_path / "state.json") == c.State()


@pytest.mark.parametrize("operation", ["load", "save", "lock"])
def test_state_and_lock_refuse_symlinks(tmp_path, operation):
    c = load_controller()
    target = tmp_path / "target"
    target.write_text("do not alter")
    link = tmp_path / "state.json"
    link.symlink_to(target)
    with pytest.raises(c.ControllerError):
        if operation == "load":
            c.load_state(link)
        elif operation == "save":
            c.save_state(link, c.State())
        else:
            with c.lock_file(link):
                pytest.fail("symlink lock must not be acquired")
    assert target.read_text() == "do not alter"


def test_state_refuses_public_file_or_writable_directory(tmp_path):
    c = load_controller()
    path = tmp_path / "state.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(c.ControllerError):
        c.load_state(path)
    path.chmod(0o600)
    tmp_path.chmod(0o777)
    with pytest.raises(c.ControllerError):
        c.save_state(path, c.State())


def test_lock_excludes_concurrent_controller_and_releases(tmp_path):
    c = load_controller()
    path = tmp_path / "controller.lock"
    with c.lock_file(path):
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(c.AlreadyRunning):
            with c.lock_file(path):
                pytest.fail("concurrent flock must fail")
    with c.lock_file(path):
        pass


def test_cli_once_executes_one_poll_and_persists_state(tmp_path, monkeypatch):
    c = load_controller()
    api = FakeAPI([c.Snapshot(queued=True)])
    vm = FakeVM("off")
    monkeypatch.setattr(c, "GitHub", lambda: api)
    monkeypatch.setattr(c, "SystemdVM", lambda: vm)
    path = tmp_path / "state.json"
    assert (
        c.main(
            [
                "--once",
                "--state",
                str(path),
                "--poll-seconds",
                "20",
                "--idle-seconds",
                "60",
                "--boot-seconds",
                "600",
            ]
        )
        == 0
    )
    assert api.calls == 1
    assert vm.actions == ["start"]
    assert c.load_state(path).phase == "starting"
    assert c.load_state(path).host_boot_id == c.host_boot_id()


def test_cli_api_failure_returns_nonzero_without_service_action(tmp_path, monkeypatch):
    c = load_controller()
    api = FakeAPI([c.ControllerError("GitHub API unavailable")])
    vm = FakeVM("off")
    monkeypatch.setattr(c, "GitHub", lambda: api)
    monkeypatch.setattr(c, "SystemdVM", lambda: vm)
    assert c.main(["--once", "--state", str(tmp_path / "state.json")]) == 1
    assert vm.actions == []


def test_cli_new_host_boot_invalidates_old_idle_state(tmp_path, monkeypatch):
    c = load_controller()
    state = c.State(
        phase="on",
        started_at=100,
        idle_since=100,
        last_seen=1000,
        host_boot_id="old-boot",
    )
    path = tmp_path / "state.json"
    c.save_state(path, state)
    monkeypatch.setattr(c, "host_boot_id", lambda: "new-boot")
    monkeypatch.setattr(c.time, "time", lambda: 1020)
    vm = FakeVM()
    monkeypatch.setattr(c, "GitHub", lambda: FakeAPI([c.Snapshot(busy=False)]))
    monkeypatch.setattr(c, "SystemdVM", lambda: vm)
    assert c.main(["--once", "--state", str(path)]) == 0
    assert c.load_state(path).started_at == 1020
    assert c.load_state(path).idle_since is None
    assert vm.actions == []


def test_cli_held_lock_exits_before_querying_github(tmp_path, monkeypatch):
    c = load_controller()
    path = tmp_path / "state.json"
    api = FakeAPI([])
    monkeypatch.setattr(c, "GitHub", lambda: api)
    with c.lock_file(str(path) + ".lock"):
        assert c.main(["--once", "--state", str(path)]) == 75
    assert api.calls == 0


@pytest.mark.parametrize(
    "option,value",
    [
        ("--poll-seconds", "0"),
        ("--idle-seconds", "-1"),
        ("--boot-seconds", "nan"),
        ("--poll-seconds", "inf"),
        ("--unit", "anything"),
        ("--repo", "attacker/repo"),
    ],
)
def test_cli_rejects_unsafe_options_before_any_operation(option, value):
    c = load_controller()
    with pytest.raises(SystemExit) as exc:
        c.main([option, value, "--once"])
    assert exc.value.code == 2


def test_cli_help_runs_as_standalone_stdlib_script():
    c = load_controller()
    result = subprocess.run(
        ["python3", c.__file__, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "--once" in result.stdout
    assert "--lockfile" in result.stdout
    assert "--boot-seconds" in result.stdout


def test_cli_refuses_state_path_as_lockfile(tmp_path, monkeypatch):
    c = load_controller()
    monkeypatch.setattr(c, "GitHub", lambda: pytest.fail("must reject before API"))
    monkeypatch.setattr(c, "SystemdVM", lambda: pytest.fail("must reject before VM"))
    path = str(tmp_path / "state.json")
    assert c.main(["--once", "--state", path, "--lockfile", path]) == 1
    assert not (tmp_path / "state.json").exists()


def test_same_host_process_restart_preserves_tracked_boot_time(tmp_path, monkeypatch):
    c = load_controller()
    path = tmp_path / "state.json"
    state = c.State(
        phase="starting", started_at=1000, last_seen=1010, host_boot_id=c.host_boot_id()
    )
    c.save_state(path, state)
    monkeypatch.setattr(c.time, "time", lambda: 1020)
    monkeypatch.setattr(c, "GitHub", lambda: FakeAPI([c.Snapshot(busy=False)]))
    monkeypatch.setattr(c, "SystemdVM", lambda: FakeVM())
    assert c.main(["--once", "--state", str(path)]) == 0
    assert c.load_state(path).started_at == 1000


@pytest.mark.parametrize("operation", ["load", "save", "lock"])
def test_relative_storage_paths_are_rejected(operation):
    c = load_controller()
    with pytest.raises(c.ControllerError):
        if operation == "load":
            c.load_state("relative-state.json")
        elif operation == "save":
            c.save_state("relative-state.json", c.State())
        else:
            with c.lock_file("relative.lock"):
                pytest.fail("relative lock must be rejected")


@pytest.mark.parametrize(
    "payload",
    [
        pages("jobs", [job(labels=None)]),
        pages("jobs", [job(labels="mu-voxl-amd64")]),
        pages("jobs", [job(status="waiting")]),
        [{"total_count": 2, "jobs": []}],
    ],
)
def test_uncertain_jobs_fail_closed(payload):
    c = load_controller()
    process = Process([pages("workflow_runs", [run_record()]), payload])
    with pytest.raises(c.ControllerError):
        c.GitHub(process=process).snapshot()


@pytest.mark.parametrize(
    "payload",
    [
        pages("runners", [runner(busy=None)]),
        pages("runners", [runner(labels="mu-voxl-amd64")]),
        subprocess.CalledProcessError(1, "gh", stderr="SECRET"),
    ],
)
def test_runner_api_failure_prevents_start_even_with_queue(payload):
    c = load_controller()
    process = Process(
        [
            pages("workflow_runs", [run_record()]),
            pages("jobs", [job()]),
            pages("workflow_runs", []),
            payload,
        ]
    )
    vm = FakeVM("off")
    ctrl = c.Controller(c.GitHub(process=process), vm)
    assert ctrl.poll(1000) is False
    assert vm.actions == []


def test_vm_becoming_uncertain_at_shutdown_recheck_is_not_stopped():
    c = load_controller()
    ctrl, _, vm = controller_for(
        [c.Snapshot(busy=False)] * 2,
        phase="on",
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    statuses = iter(["on", "unknown"])
    vm.status = lambda: next(statuses)
    assert ctrl.poll(1000) is False
    assert vm.actions == []


def test_unconfirmed_start_is_not_reported_successful():
    c = load_controller()
    ctrl, _, vm = controller_for([c.Snapshot(queued=True)], "off")
    vm.start = lambda: None
    assert ctrl.poll(1000) is False
    assert ctrl.state.idle_since is None


def test_busy_at_final_recheck_inhibits_stop_even_if_initial_scan_idle():
    c = load_controller()
    ctrl, _, vm = controller_for(
        [c.Snapshot(busy=False), c.Snapshot(busy=True)],
        phase="on",
        started_at=100,
        idle_since=900,
        last_seen=990,
    )
    assert ctrl.poll(1000) is True
    assert vm.actions == []
