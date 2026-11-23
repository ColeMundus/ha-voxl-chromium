#!/usr/bin/env python3
"""Fail if a junit report is missing a test file that git tracks.

Usage: prove_test_files.py <junit.xml> <git-glob>...
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def tracked(patterns: list[str]) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def file_from_classname(classname: str, roots: list[str]) -> str | None:
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        relative = "/".join(parts[:end]) + ".py"
        candidates = [relative, *[f"{root}/{relative}" for root in roots]]
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
    return None


def reported(path: Path, patterns: list[str]) -> set[str]:
    roots = []
    for pattern in patterns:
        parent = str(Path(pattern).parent)
        if parent not in {".", ""} and parent not in roots:
            roots.append(parent)
    root = ET.parse(path).getroot()
    files = set()
    for case in root.iter("testcase"):
        name = case.get("file")
        if name:
            files.add(name.removeprefix("./"))
            continue
        derived = file_from_classname(case.get("classname", ""), roots)
        if derived:
            files.add(derived)
    return files


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit("usage: prove_test_files.py <junit.xml> <git-glob>...")
    report = Path(argv[1])
    patterns = argv[2:]
    expected = tracked(patterns)
    seen = reported(report, patterns)
    missing = sorted(expected - seen)
    if missing:
        print("Test files missing from the junit report:", *missing, sep="\n")
        return 1
    print(f"junit covers {len(expected)} tracked test files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
