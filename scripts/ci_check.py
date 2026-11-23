#!/usr/bin/env python3
"""The local pre-push check: the same commands CI runs for lint and unit tests.

ty is included once the integration project depends on it. Pytest is the unit
suite. The integration suite stays in CI because it needs the Home Assistant
import.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def ty_is_configured() -> bool:
    project = Path("integration_tests/pyproject.toml")
    if not project.exists():
        return False
    return "ty" in project.read_text()


def main() -> int:
    commands = [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
    ]
    if ty_is_configured():
        commands.append(["uv", "run", "--project", "integration_tests", "ty", "check"])
    commands.append(["uv", "run", "pytest", "-q"])
    started = time.perf_counter()
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    elapsed = time.perf_counter() - started
    print(f"ci-check {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
