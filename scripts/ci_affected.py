#!/usr/bin/env python3
"""Decide whether a pull-request job has work to do.

The job always starts. This script prints `decision=affected` or
`decision=skipped` and a reason, and exits 0 either way, so a required check
is never left in GitHub's skipped state. Pull requests are the only events
that can skip. main, tags, schedule, workflow_dispatch and workflow_call run
every job.

Full-suite inputs (any one of them reruns every job):

- .github/**
- pyproject.toml, uv.lock
- integration_tests/pyproject.toml, integration_tests/uv.lock
- voxl_chromium/Dockerfile, voxl_chromium/upstream.json, voxl_chromium/patches/**
- ci/**
- scripts/ci_affected.py
- any top-level path that is not in KNOWN_TOP

Native jobs also run when voxl_chromium/**, tests/smoke.py,
tests/ingress_proxy.py, tests/container_checks.py or ci/** change.
Integration runs when custom_components/** or integration_tests/** change.
Unit tests and the static checks always run.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Top-level paths that exist today. A new one is a full-suite input until
# someone adds it here and decides whether it is also a narrower trigger.
KNOWN_TOP = frozenset(
    {
        ".gitattributes",
        ".github",
        ".gitignore",
        "AUTOMATION.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "SECURITY.md",
        "TESTING.md",
        "ci",
        "custom_components",
        "docs",
        "hacs.json",
        "integration_tests",
        "pyproject.toml",
        "pyrightconfig.json",
        "repository.yaml",
        "scripts",
        "tests",
        "uv.lock",
        "voxl_chromium",
    }
)

FULL_FILES = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "integration_tests/pyproject.toml",
        "integration_tests/uv.lock",
        "voxl_chromium/Dockerfile",
        "voxl_chromium/upstream.json",
        "scripts/ci_affected.py",
    }
)

NATIVE_FILES = frozenset(
    {
        "tests/smoke.py",
        "tests/ingress_proxy.py",
        "tests/container_checks.py",
    }
)


def _top(path: str) -> str:
    return path.split("/", 1)[0]


def triggers_full(path: str) -> bool:
    if path in FULL_FILES:
        return True
    if path.startswith(".github/") or path == ".github":
        return True
    if path.startswith("ci/") or path == "ci":
        return True
    if path.startswith("voxl_chromium/patches/"):
        return True
    return _top(path) not in KNOWN_TOP


def triggers_native(path: str) -> bool:
    if path in NATIVE_FILES:
        return True
    if path.startswith("voxl_chromium/") or path == "voxl_chromium":
        return True
    return path.startswith("ci/") or path == "ci"


def triggers_integration(path: str) -> bool:
    return path.startswith("custom_components/") or path.startswith(
        "integration_tests/"
    )


def decision(job: str, files: list[str] | None, event: str) -> tuple[str, str]:
    if event != "pull_request":
        return "affected", f"{event or 'local'} runs the full suite"
    if not files:
        return "affected", "empty diff runs the full suite"
    if any(triggers_full(path) for path in files):
        return "affected", "diff includes a full-suite input"
    if job in {"unit", "checks"}:
        return "affected", f"{job} always runs on a pull request"
    if job == "native" and any(triggers_native(path) for path in files):
        return "affected", "diff can reach the native image or smoke tests"
    if job == "integration" and any(triggers_integration(path) for path in files):
        return "affected", "diff can reach the Home Assistant integration"
    if job in {"native", "integration"}:
        return "skipped", f"diff cannot reach {job}"
    raise SystemExit(f"unknown job {job}")


def changed_files(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"native", "integration", "unit", "checks"}:
        raise SystemExit("usage: ci_affected.py <native|integration|unit|checks>")
    event = os.environ.get("GITHUB_EVENT_NAME", "pull_request")
    files: list[str] | None
    if event != "pull_request":
        files = None
    else:
        base = os.environ.get("AFFECTED_BASE", "origin/main")
        files = changed_files(base)
    status, reason = decision(argv[1], files, event)
    # GitHub Actions output. Values are single-line.
    print(f"decision={status}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
