"""Keep scanner execution reproducible without a shell download installer."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_smoke_installs_only_the_headless_browser_it_uses():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["container"]["steps"]
    install = next(
        step for step in steps if step.get("name") == "Install browser for smoke tests"
    )
    assert install["run"] == "uv run playwright install --only-shell chromium"
    assert "--with-deps" not in install["run"]
    cache = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/cache@")
    )
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    assert cache["with"]["key"] == (
        "playwright-${{ runner.os }}-${{ runner.arch }}-1.65.2"
    )
    build = next(
        step
        for step in steps
        if step.get("name") == "Build ephemeral source image (never pushed or uploaded)"
    )
    assert build["uses"].startswith("docker/build-push-action@")
    assert build["with"]["load"] is True
    assert build["with"]["push"] is False
    assert build["with"]["cache-from"].startswith("type=gha,scope=native-")
    assert "type=gha,mode=max,scope=native-" in build["with"]["cache-to"]
    dockerfile = (ROOT / "voxl_chromium/Dockerfile").read_text()
    assert "AS builder-apt" in dockerfile
    assert "AS runtime-apt" in dockerfile


def test_job_budgets_are_tighter_than_the_old_timeouts_and_not_35s():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["metadata"]["timeout-minutes"] == 5
    assert jobs["python"]["timeout-minutes"] == 5
    assert jobs["integration"]["timeout-minutes"] == 5
    assert jobs["container"]["timeout-minutes"] == 12
    limits = {}
    for name, job in jobs.items():
        step = next(
            item for item in job["steps"] if item.get("name") == "Enforce time budget"
        )
        limits[name] = step["env"]["LIMIT"]
        assert step["if"] == "always()"
    assert limits["metadata"] == "180"
    assert limits["python"] == "60"
    assert limits["integration"] == "120"
    assert "240" in limits["container"]
    assert "600" in limits["container"]
    assert "35" not in limits["container"]


def test_ci_uses_pinned_native_container_for_both_security_gates():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["container"]["steps"]
    scans = [step for step in steps if step.get("name", "").startswith("Scan ")]
    assert len(scans) == 2
    for step in scans:
        assert "run" in step, "Do not depend on the action's shell binary installer"
        command = step["run"]
        for required in (
            "docker run --rm",
            '"$TRIVY_IMAGE"',
            "--scanners vuln",
            "--severity HIGH,CRITICAL",
            "--ignore-unfixed",
            "--exit-code 1",
        ):
            assert required in command
        assert not step.get("continue-on-error", False)
    scanner = (ROOT / "ci/Dockerfile").read_text()
    assert re.search(r"FROM aquasec/trivy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", scanner)
    updates = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
    assert any(
        item["package-ecosystem"] == "docker" and item["directory"] == "/ci"
        for item in updates
    )
