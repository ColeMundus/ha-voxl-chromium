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
    assert install["run"] == (
        "uv run playwright install --with-deps --only-shell chromium"
    )


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
