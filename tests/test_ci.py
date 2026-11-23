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


def test_checks_job_reports_every_static_failure():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["checks"]["steps"]
    for step_id in ("ruff", "ruff-format", "addon", "hassfest"):
        step = next(item for item in steps if item.get("id") == step_id)
        assert step["continue-on-error"] is True
    report = next(
        item for item in steps if item.get("name") == "Report every failed check"
    )
    assert report["if"] == "always()"
    for label in ("ruff check", "ruff format", "addon-linter", "hassfest"):
        assert label in report["run"]
    hacs = workflow["jobs"]["hacs"]
    validate = next(
        item
        for item in hacs["steps"]
        if item.get("name") == "Validate HACS custom repository"
    )
    assert "ghcr.io/hacs/action@sha256:187499a09c5d" in validate["run"]
    assert "INPUT_CATEGORY=integration" in validate["run"]
    assert hacs["timeout-minutes"] == 5
    restore = next(
        item
        for item in hacs["steps"]
        if item.get("name") == "Restore pinned HACS image"
    )
    assert restore["with"]["key"].startswith("hacs-action-")
    python_runs = [step.get("run", "") for step in workflow["jobs"]["python"]["steps"]]
    assert all("ruff" not in run for run in python_runs)


def test_job_budgets_are_tighter_than_the_old_timeouts_and_not_35s():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["checks"]["timeout-minutes"] == 5
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
    assert limits["checks"] == "180"
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
        assert "trivy-db:/root/.cache/" in command
        assert "voxl-trivy-cache" not in command
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'sudo chmod -R a+rX "${{ runner.temp }}/trivy-db"' in text
    assert "key: trivy-db-${{ matrix.arch }}-${{ steps.trivy-day.outputs.day }}" in text
    scanner = (ROOT / "ci/Dockerfile").read_text()
    assert re.search(r"FROM aquasec/trivy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", scanner)
    updates = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
    assert any(
        item["package-ecosystem"] == "docker" and item["directory"] == "/ci"
        for item in updates
    )
