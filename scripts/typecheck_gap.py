#!/usr/bin/env python3
"""Print pyright diagnostics whose file and line ty did not report.

ty is the required typecheck. pyright runs beside it so a miss is visible.
This script exits 0 after printing the difference. A ty failure is reported
by the ty step, not by hiding it here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

TY_LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):\d+:")


def ty_lines() -> set[tuple[str, int]]:
    result = subprocess.run(
        ["ty", "check", "--output-format", "concise"],
        capture_output=True,
        text=True,
        check=False,
    )
    found = set()
    for line in (result.stdout + result.stderr).splitlines():
        match = TY_LINE.match(line)
        if match:
            found.add((match.group("file"), int(match.group("line"))))
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return found


def pyright_diagnostics() -> list[tuple[str, int, str, str]]:
    result = subprocess.run(
        ["pyright", "--outputjson"],
        capture_output=True,
        text=True,
        check=False,
    )
    data = json.loads(result.stdout or "{}")
    rows = []
    for item in data.get("generalDiagnostics", []):
        path = os.path.relpath(item["file"], os.getcwd())
        line = item["range"]["start"]["line"] + 1
        rows.append((path, line, item.get("rule", ""), item["message"].split("\n")[0]))
    return rows


def main() -> int:
    covered = ty_lines()
    missed = []
    for path, line, rule, message in pyright_diagnostics():
        if (path, line) not in covered:
            missed.append((path, line, rule, message))
    print(f"pyright diagnostics not reported by ty: {len(missed)}")
    for path, line, rule, message in missed:
        print(f"{path}:{line} {rule}: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
