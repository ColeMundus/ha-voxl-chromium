#!/usr/bin/env python3
"""On-demand runner controller. Credentials remain in the host's gh configuration."""

import argparse
import fcntl
import json
import logging
import math
import os
import secrets
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY = "ColeMundus/ha-voxl-chromium"
LABEL = "mu-voxl-amd64"
WORKFLOW = ".github/workflows/ci.yml"
UNIT = "gh-voxl-vm.service"


class ControllerError(Exception):
    """A sanitized operational error, safe to log without process output."""


@dataclass(frozen=True)
class Snapshot:
    queued: bool = False
    in_progress: bool = False
    busy: bool | None = None


class GitHub:
    def __init__(self, process=subprocess.run):
        self.process = process

    def _list(self, endpoint, key):
        try:
            result = self.process(
                ["gh", "api", "--paginate", endpoint],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            # Ubuntu's gh 2.45 has no --slurp: decode the complete stream of
            # paginated JSON objects without relying on line boundaries.
            decoder = json.JSONDecoder()
            raw = result.stdout
            data = []
            offset = 0
            while offset < len(raw):
                if raw[offset].isspace():
                    offset += 1
                    continue
                page, offset = decoder.raw_decode(raw, offset)
                data.append(page)
            if not data:
                raise ValueError
            items = []
            expected = 0
            for page in data:
                if not isinstance(page, dict):
                    raise ValueError
                count = page.get("total_count")
                entries = page.get(key)
                if type(count) is not int or count < 0 or not isinstance(entries, list):
                    raise ValueError
                if not all(isinstance(item, dict) for item in entries):
                    raise ValueError
                items.extend(entries)
                expected = max(expected, count)
            if len(items) < expected:
                raise ValueError
            return items
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            raise ControllerError(
                "GitHub API failed or returned incomplete data"
            ) from None

    @staticmethod
    def trusted(run):
        repository = run.get("head_repository")
        return (
            run.get("name") == "CI"
            and run.get("path") in (WORKFLOW, WORKFLOW + "@refs/heads/main")
            and run.get("head_branch") == "main"
            and isinstance(repository, dict)
            and repository.get("full_name") == REPOSITORY
            and run.get("event") in ("push", "workflow_dispatch", "schedule")
        )

    def snapshot(self):
        queued = in_progress = False
        for status in ("queued", "in_progress"):
            runs = self._list(
                f"repos/{REPOSITORY}/actions/runs?status={status}&per_page=100",
                "workflow_runs",
            )
            for run in runs:
                if not self.trusted(run):
                    continue
                run_id = run.get("id")
                if type(run_id) is not int or run_id <= 0:
                    raise ControllerError("GitHub returned an invalid run ID")
                jobs = self._list(
                    f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100",
                    "jobs",
                )
                for job in jobs:
                    labels = job.get("labels")
                    if not isinstance(labels, list) or not all(
                        isinstance(label, str) for label in labels
                    ):
                        raise ControllerError("GitHub returned invalid job labels")
                    if LABEL in labels:
                        status = job.get("status")
                        if status not in ("queued", "in_progress", "completed"):
                            raise ControllerError(
                                "GitHub returned uncertain job status"
                            )
                        queued |= status == "queued"
                        in_progress |= status == "in_progress"
        runners = self._list(
            f"repos/{REPOSITORY}/actions/runners?per_page=100", "runners"
        )
        matches = []
        for runner in runners:
            labels = runner.get("labels")
            if not isinstance(labels, list) or not all(
                isinstance(label, dict) and isinstance(label.get("name"), str)
                for label in labels
            ):
                raise ControllerError("GitHub returned invalid runner labels")
            if any(label["name"] == LABEL for label in labels):
                if type(runner.get("busy")) is not bool:
                    raise ControllerError("GitHub returned uncertain runner activity")
                matches.append(runner)
        busy = None
        if any(runner["busy"] for runner in matches):
            busy = True
        elif matches and all(runner.get("status") == "online" for runner in matches):
            busy = False
        return Snapshot(queued, in_progress, busy)


class SystemdVM:
    """The only privileged interface is two fixed sudoers-approved commands."""

    def __init__(self, process=subprocess.run):
        self.process = process

    def _run(self, args, *, check=True, timeout=30):
        try:
            return self.process(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=check,
            )
        except (OSError, subprocess.SubprocessError):
            raise ControllerError("VM systemctl operation failed") from None

    def status(self):
        result = self._run(
            ["/usr/bin/systemctl", "is-active", "--quiet", UNIT], check=False
        )
        if result.returncode == 0:
            return "on"
        if result.returncode != 3:
            return "unknown"
        # --quiet cannot distinguish an inactive VM from an activating service.
        detail = self._run(
            ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", UNIT]
        )
        return {
            "inactive": "off",
            "failed": "off",
            "active": "on",
            "activating": "starting",
        }.get(detail.stdout.strip(), "unknown")

    def start(self):
        self._run(["sudo", "-n", "/usr/bin/systemctl", "start", UNIT], timeout=120)

    def stop(self):
        self._run(["sudo", "-n", "/usr/bin/systemctl", "stop", UNIT], timeout=120)


@dataclass
class State:
    """Epoch timestamps survive process restarts; discontinuities renew grace."""

    phase: str = "off"
    started_at: float | None = None
    idle_since: float | None = None
    last_seen: float | None = None
    host_boot_id: str | None = None


class Controller:
    def __init__(
        self, api, vm, state=None, idle_seconds=60, boot_seconds=600, poll_seconds=20
    ):
        self.api = api
        self.vm = vm
        self.state = state if state is not None else State()
        self.idle_seconds = idle_seconds
        self.boot_seconds = boot_seconds
        self.poll_seconds = poll_seconds
        self.last_error = None

    def _phase(self, phase):
        if self.state.phase != phase:
            logging.info("VM %s -> %s", self.state.phase, phase)
            self.state.phase = phase

    @staticmethod
    def _idle(snapshot):
        return (
            not snapshot.queued and not snapshot.in_progress and snapshot.busy is False
        )

    def poll(self, now):
        """Return False on uncertainty. No error path is allowed to stop the VM."""
        state = self.state
        try:
            status = self.vm.status()
            if status not in ("off", "starting", "on"):
                raise ControllerError("VM state is uncertain")
            if status == "off":
                self._phase("off")
                state.started_at = state.idle_since = None
            else:
                discontinuity = (
                    state.last_seen is None
                    or now < state.last_seen
                    or now - state.last_seen
                    > max(self.idle_seconds, self.poll_seconds * 3)
                )
                if (
                    state.phase == "off"
                    or state.started_at is None
                    or state.started_at > now
                    or discontinuity
                ):
                    state.started_at = now
                    state.idle_since = None
                    self._phase("starting")
            state.last_seen = now
            snapshot = self.api.snapshot()
            if status == "off":
                if snapshot.queued:
                    state.started_at = now
                    self._phase("starting")
                    self.vm.start()
                    if self.vm.status() not in ("starting", "on"):
                        raise ControllerError("VM start could not be confirmed")
                return self._success()
            if snapshot.in_progress or snapshot.busy is True:
                self._phase("on")
            if status == "starting":
                state.idle_since = None
                return self._success()
            if state.phase == "starting":
                if now - state.started_at < self.boot_seconds:
                    state.idle_since = None
                    return self._success()
                self._phase("on")
            if not self._idle(snapshot):
                state.idle_since = None
                return self._success()
            if state.idle_since is None or state.idle_since > now:
                state.idle_since = now
                logging.info("VM idle grace started")
            if now - state.idle_since >= self.idle_seconds:
                # Fresh full GitHub read (including busy), immediately before stop.
                # GitHub dispatch cannot be made atomic with systemd shutdown.
                final = self.api.snapshot()
                if not self._idle(final):
                    state.idle_since = None
                    return self._success()
                if self.vm.status() != "on":
                    raise ControllerError("VM state changed before shutdown")
                self.vm.stop()
                if self.vm.status() != "off":
                    raise ControllerError("VM stop could not be confirmed")
                self._phase("off")
                state.started_at = state.idle_since = None
            return self._success()
        except ControllerError as exc:
            state.idle_since = None
            message = str(exc)
            if self.last_error != message:
                logging.warning("Controller failsafe: %s", message)
                self.last_error = message
            return False

    def _success(self):
        if self.last_error is not None:
            logging.info("Controller recovered")
            self.last_error = None
        return True


class AlreadyRunning(ControllerError):
    """Another process holds the controller lock."""


@contextmanager
def _directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ControllerError("State and lock paths must be absolute")
    fd = None
    try:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ControllerError(
                "State directory must be owned and not writable by others"
            )
        yield fd, path.name
    except OSError:
        raise ControllerError("Cannot access private controller storage") from None
    finally:
        if fd is not None:
            os.close(fd)


def _private_open(directory, name, flags):
    fd = os.open(
        name,
        flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        0o600,
        dir_fd=directory,
    )
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise ControllerError("Controller storage must be private regular files")
    return fd


def load_state(path):
    with _directory(path) as (directory, name):
        try:
            fd = _private_open(directory, name, os.O_RDONLY)
        except FileNotFoundError:
            return State()
        with os.fdopen(fd, encoding="utf-8") as stream:
            raw = stream.read(65537)
        try:
            if len(raw) > 65536:
                raise ValueError
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            state = State(**data)
            if state.phase not in ("off", "starting", "on"):
                raise ValueError
            for value in (state.started_at, state.idle_since, state.last_seen):
                if value is not None and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError
            for value in (state.started_at, state.idle_since):
                if value is not None and (
                    state.last_seen is None or value > state.last_seen
                ):
                    raise ValueError
            if state.host_boot_id is not None and not isinstance(
                state.host_boot_id, str
            ):
                raise ValueError
            return state
        except (ValueError, TypeError):
            logging.warning("Invalid controller state; renewing startup grace")
            return State()


def save_state(path, state):
    with _directory(path) as (directory, name):
        try:
            old = _private_open(directory, name, os.O_RDONLY)
        except FileNotFoundError:
            pass
        else:
            os.close(old)
        temporary = f".{name}.{secrets.token_hex(8)}"
        try:
            fd = _private_open(
                directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(state), stream, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


@contextmanager
def lock_file(path):
    # Never unlink the lock: a replacement inode would permit a second controller.
    with _directory(path) as (directory, name):
        fd = _private_open(directory, name, os.O_RDWR | os.O_CREAT)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AlreadyRunning("Another controller holds the lock") from None
            yield
        finally:
            os.close(fd)


def host_boot_id():
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not value:
            raise ValueError
        return value
    except (OSError, ValueError):
        raise ControllerError("Cannot determine host boot identity") from None


def _positive_seconds(value):
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("must be finite positive seconds") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/gh-voxl-controller/state.json")
    )
    parser.add_argument(
        "--lockfile",
        type=Path,
        help="defaults to STATE.lock; directory must already exist",
    )
    parser.add_argument("--poll-seconds", type=_positive_seconds, default=20)
    parser.add_argument("--idle-seconds", type=_positive_seconds, default=60)
    parser.add_argument("--boot-seconds", type=_positive_seconds, default=600)
    parser.add_argument("--once", action="store_true", help="poll once, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lock = (
        args.lockfile if args.lockfile is not None else Path(str(args.state) + ".lock")
    )
    try:
        if lock == args.state:
            raise ControllerError("State and lock must use different files")
        with lock_file(lock):
            boot = host_boot_id()
            state = load_state(args.state)
            if state.host_boot_id != boot:
                state = State(host_boot_id=boot)
            controller = Controller(
                GitHub(),
                SystemdVM(),
                state,
                args.idle_seconds,
                args.boot_seconds,
                args.poll_seconds,
            )
            while True:
                ok = controller.poll(time.time())
                save_state(args.state, controller.state)
                if args.once:
                    return 0 if ok else 1
                time.sleep(args.poll_seconds)
    except AlreadyRunning:
        logging.warning("Another controller holds the lock")
        return 75
    except (ControllerError, OSError, ValueError):
        logging.error("Controller stopped: storage or configuration failure")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
