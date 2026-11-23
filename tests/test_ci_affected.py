"""The pull-request selector narrows jobs and never drops the full suite."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_affected", ROOT / "scripts" / "ci_affected.py"
)
ci_affected = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_affected)


def outcome(job, files, event="pull_request"):
    return ci_affected.decision(job, files, event)


def test_known_top_level_paths_match_the_tree():
    listed = subprocess.check_output(
        ["git", "ls-tree", "--name-only", "HEAD"], cwd=ROOT, text=True
    ).split()
    assert set(listed) == set(ci_affected.KNOWN_TOP)


@pytest.mark.parametrize(
    "event", ["push", "schedule", "workflow_dispatch", "workflow_call", ""]
)
def test_non_pull_requests_run_every_job(event):
    assert outcome("native", ["README.md"], event)[0] == "affected"
    assert outcome("integration", ["README.md"], event)[0] == "affected"


def test_docs_only_skips_native_and_integration_but_not_unit_or_checks():
    files = ["README.md", "docs/screenshots/browser.jpg", "AUTOMATION.md"]
    assert outcome("native", files)[0] == "skipped"
    assert outcome("integration", files)[0] == "skipped"
    assert outcome("unit", files)[0] == "affected"
    assert outcome("checks", files)[0] == "affected"


def test_addon_python_runs_native_not_integration():
    files = ["voxl_chromium/control.py"]
    assert outcome("native", files)[0] == "affected"
    assert outcome("integration", files)[0] == "skipped"


def test_smoke_helpers_run_native():
    for path in (
        "tests/smoke.py",
        "tests/ingress_proxy.py",
        "tests/container_checks.py",
    ):
        assert outcome("native", [path])[0] == "affected"


def test_integration_paths_skip_native():
    files = [
        "custom_components/voxl_chromium/client.py",
        "integration_tests/test_input.py",
    ]
    assert outcome("integration", files)[0] == "affected"
    assert outcome("native", files)[0] == "skipped"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "pyproject.toml",
        "uv.lock",
        "integration_tests/pyproject.toml",
        "integration_tests/uv.lock",
        "voxl_chromium/Dockerfile",
        "voxl_chromium/upstream.json",
        "voxl_chromium/patches/zip-security.patch",
        "ci/Dockerfile",
        "scripts/ci_affected.py",
        "brand-new-top-level",
    ],
)
def test_full_suite_inputs_run_every_job(path):
    assert outcome("native", [path])[0] == "affected"
    assert outcome("integration", [path])[0] == "affected"
    assert "full-suite" in outcome("native", [path])[1]


def test_watched_native_file_cannot_hide_from_the_selector():
    """A behavior change in a watched file cannot hide. An untracked top-level
    name cannot hide either: it reruns everything."""
    hidden_style = ["tests/ingress_proxy.py"]
    assert outcome("native", hidden_style)[0] == "affected"
    assert outcome("native", ["not-a-known-path/secret_change.py"])[0] == "affected"
