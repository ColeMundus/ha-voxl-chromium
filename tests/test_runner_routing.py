"""Keep CI on disposable GitHub-hosted runners for every trigger."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def workflow(name="ci"):
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())


def test_native_builds_use_only_github_hosted_runners():
    job = workflow()["jobs"]["container"]
    assert job["runs-on"] == "${{ matrix.runner }}"
    entries = job["strategy"]["matrix"]["include"]
    assert entries == [
        {"arch": "amd64", "runner": "ubuntu-24.04"},
        {"arch": "aarch64", "runner": "ubuntu-24.04-arm"},
    ]
    assert job["strategy"]["fail-fast"] is False


def test_all_other_jobs_use_github_hosted_runners_or_reusable_workflows():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        for job in yaml.safe_load(path.read_text())["jobs"].values():
            if "uses" in job:
                assert "runs-on" not in job
            elif job.get("runs-on") != "${{ matrix.runner }}":
                assert job["runs-on"] in {"ubuntu-24.04", "ubuntu-24.04-arm"}


def test_hosted_jobs_need_no_persistent_runner_management():
    definition = workflow()
    assert "concurrency" not in definition
    job = definition["jobs"]["container"]
    assert "concurrency" not in job
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    for obsolete in (
        "self-hosted",
        "mu-voxl",
        "VOXL_JOB_TEMP_NAME",
        "Clean persistent workspace",
    ):
        assert obsolete not in text
    for job in definition["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_release_reuses_the_same_hosted_ci():
    assert workflow("release")["jobs"]["tests"]["uses"] == (
        "./.github/workflows/ci.yml"
    )
