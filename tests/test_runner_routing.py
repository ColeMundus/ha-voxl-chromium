"""Parse the real workflow and fail closed on changes to its trust boundary."""

import itertools
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SELF_HOSTED = ["self-hosted", "linux", "x64", "mu-voxl-amd64"]
TRUSTED_PREDICATE = (
    "github.repository == 'ColeMundus/ha-voxl-chromium' && "
    "github.ref == 'refs/heads/main' && "
    "(github.event_name == 'push' || "
    "github.event_name == 'workflow_dispatch' || github.event_name == 'schedule')"
)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


def runner_for(repository, ref, event):
    """Evaluate only the explicitly approved expression, never arbitrary eval.

    GitHub supplies the expression engine; actionlint validates its syntax. This
    helper checks the exact trust predicate and reads both JSON branches from
    the actual YAML before exercising the event/ref/repository routing table.
    """
    job = workflow()["jobs"]["container"]
    assert job["runs-on"] == "${{ matrix.runner }}"
    entries = {row["arch"]: row for row in job["strategy"]["matrix"]["include"]}
    expression = entries["amd64"]["runner"]
    match = re.fullmatch(
        r"\$\{\{\s*fromJSON\((.*?)&&\s*'(\[.*?\])'\s*\|\|\s*'(\[.*?\])'\)\s*\}\}",
        expression.strip(),
        re.DOTALL,
    )
    assert match, "amd64 must use a conditional fromJSON runner-label array"
    predicate, trusted_json, hosted_json = match.groups()
    assert " ".join(predicate.split()) == TRUSTED_PREDICATE
    trusted = (
        repository == "ColeMundus/ha-voxl-chromium"
        and ref == "refs/heads/main"
        and event in {"push", "workflow_dispatch", "schedule"}
    )
    return json.loads(trusted_json if trusted else hosted_json)


@pytest.mark.parametrize(
    ("repository", "ref", "event"),
    list(
        itertools.product(
            ["ColeMundus/ha-voxl-chromium", "contributor/ha-voxl-chromium"],
            [
                "refs/heads/main",
                "refs/heads/feature",
                "refs/heads/dependabot/pip/pytest-9",
                "refs/pull/42/merge",
                "refs/tags/v0.1.0",
            ],
            [
                "push",
                "workflow_dispatch",
                "schedule",
                "pull_request",
                "pull_request_target",
                "workflow_call",
                "release",
                "repository_dispatch",
                "unknown",
            ],
        )
    ),
)
def test_only_canonical_trusted_main_events_use_mu(repository, ref, event):
    expected = (
        SELF_HOSTED
        if repository == "ColeMundus/ha-voxl-chromium"
        and ref == "refs/heads/main"
        and event in {"push", "workflow_dispatch", "schedule"}
        else ["ubuntu-24.04"]
    )
    assert runner_for(repository, ref, event) == expected


def test_persistent_workspace_cleanup_is_scoped_and_surrounds_checkout():
    job = workflow()["jobs"]["container"]
    steps = job["steps"]
    checkout_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert steps[checkout_index]["with"]["persist-credentials"] is False
    assert job.get("env", {}).get("VOXL_JOB_TEMP_NAME") == (
        "voxl-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.arch }}"
    )
    # runner context is unavailable at jobs.<job>.env; use shell RUNNER_TEMP.
    assert all("runner." not in value for value in job.get("env", {}).values())
    before, after = steps[0], steps[-1]
    assert checkout_index > 0
    assert before.get("if") == "contains(matrix.runner, 'self-hosted')"
    assert after.get("if") == "always() && contains(matrix.runner, 'self-hosted')"
    assert before["run"] == after["run"]
    assert any(
        step.get("name") == "Prepare job temporary directory"
        for step in steps[1:checkout_index]
    )


@pytest.mark.parametrize("position", [0, -1])
def test_cleanup_removes_only_this_workspace_and_job_temp(tmp_path, position):
    import os
    import subprocess

    step = workflow()["jobs"]["container"]["steps"][position]
    assert step.get("name", "").startswith("Clean persistent workspace")
    assert "run" in step, "Persistent runners need pre- and post-job cleanup"
    workspace = tmp_path / "work" / "checkout"
    runner_temp = tmp_path / "temp"
    job_temp = runner_temp / "voxl-123-1-amd64"
    other_job = runner_temp / "another-job"
    outside = tmp_path / "outside"
    for directory in (workspace, job_temp, other_job, outside):
        directory.mkdir(parents=True)
        (directory / ".hidden-artifact").write_text("keep outside target")
    (workspace / "outside-link").symlink_to(outside, target_is_directory=True)
    # Do not invoke real sudo during tests; run real find/rm on the fixture only.
    command = 'sudo() { test "$1" = -n && shift; "$@"; }\n' + step["run"]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        env={
            **os.environ,
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(runner_temp),
            "TMPDIR": str(job_temp),
            "VOXL_JOB_TEMP_NAME": job_temp.name,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(workspace.iterdir()) == []
    assert not job_temp.exists()
    assert (other_job / ".hidden-artifact").is_file()
    assert (outside / ".hidden-artifact").is_file()


@pytest.mark.parametrize("position", [0, -1])
def test_cleanup_rejects_temp_path_escape_before_removing_workspace(tmp_path, position):
    import os
    import subprocess

    step = workflow()["jobs"]["container"]["steps"][position]
    assert step.get("name", "").startswith("Clean persistent workspace")
    workspace = tmp_path / "checkout"
    runner_temp = tmp_path / "temp"
    workspace.mkdir()
    runner_temp.mkdir()
    sentinel = workspace / "keep"
    sentinel.write_text("Do not delete when validation fails")
    # sudo must not be reached for an invalid scope, even on non-root CI hosts.
    command = 'sudo() { printf "unexpected sudo" >&2; return 99; }\n' + step["run"]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", command],
        env={
            **os.environ,
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(runner_temp),
            "VOXL_JOB_TEMP_NAME": "../outside",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unexpected sudo" not in result.stderr
    assert sentinel.is_file()


def test_prepare_step_exports_existing_job_specific_tmpdir(tmp_path):
    import os
    import subprocess

    steps = workflow()["jobs"]["container"]["steps"]
    step = next(s for s in steps if s.get("name") == "Prepare job temporary directory")
    environment = tmp_path / "github-env"
    job_temp = tmp_path / "voxl-123-1-amd64"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "VOXL_JOB_TEMP_NAME": job_temp.name,
            "GITHUB_ENV": str(environment),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert job_temp.is_dir()
    assert environment.read_text() == f"TMPDIR={job_temp}\n"


def test_only_mu_jobs_share_a_non_cancelling_concurrency_group():
    definition = workflow()
    assert "concurrency" not in definition, "Do not cancel or serialize all CI runs"
    concurrency = definition["jobs"]["container"].get("concurrency", {})
    assert concurrency.get("cancel-in-progress") is False
    assert " ".join(concurrency.get("group", "").split()) == (
        "${{ contains(matrix.runner, 'mu-voxl-amd64') && "
        "'voxl-mu-amd64-smoke' || "
        "format('voxl-hosted-{0}-{1}-{2}', "
        "github.run_id, github.run_attempt, matrix.arch) }}"
    )


def test_arm_and_lightweight_jobs_remain_github_hosted():
    jobs = workflow()["jobs"]
    entries = {
        row["arch"]: row for row in jobs["container"]["strategy"]["matrix"]["include"]
    }
    assert set(entries) == {"amd64", "aarch64"}
    assert entries["aarch64"]["runner"] == "ubuntu-24.04-arm"
    for name in ("metadata", "python"):
        assert jobs[name]["runs-on"] == "ubuntu-24.04"


def test_reusable_release_tags_are_hosted_even_with_inherited_push_event():
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert release["jobs"]["tests"]["uses"] == "./.github/workflows/ci.yml"
    # Reusable workflows inherit the caller's event/ref, not necessarily workflow_call.
    assert runner_for("ColeMundus/ha-voxl-chromium", "refs/tags/v0.1.0", "push") == [
        "ubuntu-24.04"
    ]
