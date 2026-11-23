"""The junit proof fails when a tracked test file never ran."""

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "prove_test_files", ROOT / "scripts" / "prove_test_files.py"
)
if spec is None or spec.loader is None:
    raise RuntimeError("prove_test_files could not be loaded")
prove = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prove)


def write_report(path: Path, files: list[str]) -> None:
    root = ET.Element("testsuite")
    for name in files:
        ET.SubElement(root, "testcase", file=name, name="t", classname="t")
    ET.ElementTree(root).write(path)


def test_real_report_covers_tracked_files(tmp_path):
    report = tmp_path / "junit.xml"
    files = prove.tracked(["tests/test_*.py"])
    write_report(report, sorted(files))
    assert prove.main(["prove", str(report), "tests/test_*.py"]) == 0


def test_classname_without_package_resolves_under_the_glob(tmp_path):
    report = tmp_path / "junit.xml"
    root = ET.Element("testsuite")
    ET.SubElement(root, "testcase", classname="test_boundaries", name="t")
    ET.ElementTree(root).write(report)
    seen = prove.reported(report, ["integration_tests/test_*.py"])
    assert seen == {"integration_tests/test_boundaries.py"}


def test_missing_file_fails(tmp_path):
    report = tmp_path / "junit.xml"
    files = sorted(prove.tracked(["tests/test_*.py"]))
    write_report(report, files[:-1])
    assert prove.main(["prove", str(report), "tests/test_*.py"]) == 1


def test_pytest_junit_uses_repo_relative_file_names(tmp_path):
    report = tmp_path / "junit.xml"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pytest",
            "--junitxml",
            str(report),
            "tests/test_readme_badges.py",
            "-q",
        ],
        cwd=ROOT,
    )
    seen = prove.reported(report, ["tests/test_*.py"])
    assert "tests/test_readme_badges.py" in seen
