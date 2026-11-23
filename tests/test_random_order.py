"""Random order is on, and the seed is part of the pytest report."""

import subprocess
import sys


def test_randomly_plugin_is_active(pytestconfig):
    assert pytestconfig.pluginmanager.has_plugin("randomly")


def test_randomly_prints_the_seed():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_readme_badges.py",
            "-p",
            "randomly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "--randomly-seed=" in combined
