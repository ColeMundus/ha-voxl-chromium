"""Offline contract tests for the source-only release bundle."""

import hashlib
import importlib
import importlib.util
import io
import json
import runpy
import shutil
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_COMMIT = "c9b42e6567095cd0f9a23bf2ba8684daaa0464a5"
PROJECT_COMMIT = "a" * 40
VERSION = "1.2.3"
ORIGINAL_LOCK = b"# Original upstream lock, before local patches\n"
NOTICE = (
    "Source-build-only release. No container images or binaries are distributed.\n"
    "Includes tracked project source, the exact upstream archive, and Cargo.lock.\n"
    "The project source includes local patches; apply them before building.\n"
    "upstream-Cargo.lock is the ORIGINAL lock; "
    "local patches define the effective lock.\n"
    "Cargo.lock records dependency versions; dependency sources are not vendored.\n"
    "This convenience bundle does not claim to be complete corresponding source\n"
    "for Chromium, Debian, Rust dependencies, or any locally built binary image.\n"
    "Review dependency licenses and source-delivery obligations before "
    "redistribution.\n"
)


def packager():
    assert importlib.util.find_spec("scripts.package_sources") is not None, (
        "Source packaging must be extracted into scripts/package_sources.py"
    )
    return importlib.import_module("scripts.package_sources")


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    addon = root / "voxl_chromium"
    addon.mkdir(parents=True)
    shutil.copytree(ROOT / "voxl_chromium/patches", addon / "patches")
    output = tmp_path / "assets"
    output.mkdir()
    project_archive = output / f"ha-voxl-chromium-{VERSION}-source.tar.gz"
    project_archive.write_bytes(b"already created by git archive")
    archive = tmp_path / "download.tar.gz"
    with tarfile.open(archive, "w:gz") as source:
        member = tarfile.TarInfo(f"voxl-{UPSTREAM_COMMIT}/Cargo.lock")
        member.size = len(ORIGINAL_LOCK)
        source.addfile(member, io.BytesIO(ORIGINAL_LOCK))
    pin = {
        "version": "0.3.2",
        "commit": UPSTREAM_COMMIT,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    (addon / "upstream.json").write_text(json.dumps(pin))
    calls = []

    def download(command, *, check):
        calls.append((command, check))
        shutil.copyfile(archive, command[command.index("--output") + 1])

    monkeypatch.setattr("subprocess.run", download)
    return root, output, archive, pin, calls


def test_packages_original_lock_manifest_patches_notice_and_checksums(bundle):
    root, output, archive, pin, calls = bundle
    packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)

    upstream_name = f"voxl-{UPSTREAM_COMMIT}-source.tar.gz"
    assert calls == [
        (
            [
                "curl",
                "--fail",
                "--location",
                "--proto",
                "=https",
                "--tlsv1.2",
                "--retry",
                "3",
                "--max-time",
                "300",
                "--output",
                str(output / upstream_name),
                f"https://github.com/tembo/voxl/archive/{UPSTREAM_COMMIT}.tar.gz",
            ],
            True,
        )
    ]
    assert (output / upstream_name).read_bytes() == archive.read_bytes()
    assert (output / f"ha-voxl-chromium-{VERSION}-source.tar.gz").read_bytes() == (
        b"already created by git archive"
    )
    assert (output / "upstream-Cargo.lock").read_bytes() == ORIGINAL_LOCK
    patches = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "voxl_chromium/patches").glob("*.patch"))
    }
    assert patches  # Exercise the actual tracked local patches, not an empty fixture.
    assert json.loads((output / "source-manifest.json").read_text()) == {
        "project_commit": PROJECT_COMMIT,
        "project_version": VERSION,
        "upstream": pin,
        "patches": patches,
        "distribution": "source-build-only",
    }
    assert (output / "SOURCE-NOTICE.txt").read_text() == NOTICE
    expected = {
        upstream_name,
        f"ha-voxl-chromium-{VERSION}-source.tar.gz",
        "upstream-Cargo.lock",
        "source-manifest.json",
        "SOURCE-NOTICE.txt",
    }
    lines = (output / "SHA256SUMS").read_text().splitlines()
    assert lines == [
        f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}"
        for name in sorted(expected)
    ]
    assert {path.name for path in output.iterdir()} == expected | {"SHA256SUMS"}


def test_checksums_exclude_self_and_preserve_existing_regular_file_scan(bundle):
    root, output, _, _, _ = bundle
    (output / "stale-unrelated.txt").write_bytes(b"existing file")
    (output / "directory").mkdir()
    (output / "SHA256SUMS").write_text("old checksums\n")
    module = packager()
    module.package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    first = (output / "SHA256SUMS").read_bytes()
    expected = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    }
    assert first.decode().splitlines() == [
        f"{expected[name]}  {name}" for name in sorted(expected)
    ]
    assert "stale-unrelated.txt" in expected
    module.package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert (output / "SHA256SUMS").read_bytes() == first


def test_cli_packages_with_explicit_fixture_paths(bundle, monkeypatch):
    root, output, _, _, _ = bundle
    module = packager()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module.__file__,
            "--version",
            VERSION,
            "--commit",
            PROJECT_COMMIT,
            "--root",
            str(root),
            "--output",
            str(output),
        ],
    )
    runpy.run_path(module.__file__, run_name="__main__")
    assert (output / "SOURCE-NOTICE.txt").read_text() == NOTICE


def test_cli_defaults_to_repository_and_dist(monkeypatch):
    module = packager()
    calls = []
    monkeypatch.setattr(
        module, "package_sources", lambda *a, **kw: calls.append((a, kw))
    )
    module.main(["--version", VERSION, "--commit", PROJECT_COMMIT])
    assert calls == [((VERSION, PROJECT_COMMIT), {"root": ROOT, "output": None})]


def test_workflow_calls_stdlib_helper_without_requiring_uv_in_sources_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["sources"]["steps"]
        if step.get("name", "").startswith("Fetch and verify")
    )
    assert step["run"].strip() == (
        'python3 scripts/package_sources.py --version "$RELEASE_VERSION" '
        '--commit "$GITHUB_SHA"'
    )


def test_checksum_rejection_happens_before_tar_read_or_derived_outputs(
    bundle,
    monkeypatch,
):
    root, output, _, pin, _ = bundle
    pin["sha256"] = "0" * 64
    (root / "voxl_chromium/upstream.json").write_text(json.dumps(pin))

    def forbidden(*args, **kwargs):
        pytest.fail("Unverified archive was opened as a tar file")

    monkeypatch.setattr(tarfile, "open", forbidden)
    with pytest.raises(SystemExit, match="Upstream archive checksum mismatch"):
        packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert {path.name for path in output.iterdir()} == {
        f"ha-voxl-chromium-{VERSION}-source.tar.gz",
        f"voxl-{UPSTREAM_COMMIT}-source.tar.gz",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("commit", "b" * 39, "Invalid upstream commit"),
        ("commit", "B" * 40, "Invalid upstream commit"),
        ("commit", "../" + "a" * 40, "Invalid upstream commit"),
        ("sha256", "f" * 63, "Invalid upstream archive SHA-256"),
        ("sha256", "G" * 64, "Invalid upstream archive SHA-256"),
    ],
)
def test_malformed_pin_is_rejected_before_download(bundle, field, value, message):
    root, output, _, pin, calls = bundle
    pin[field] = value
    (root / "voxl_chromium/upstream.json").write_text(json.dumps(pin))
    with pytest.raises(SystemExit, match=message):
        packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert calls == []
    assert {path.name for path in output.iterdir()} == {
        f"ha-voxl-chromium-{VERSION}-source.tar.gz",
    }


def test_invalid_json_is_rejected_before_download(bundle):
    root, output, _, _, calls = bundle
    (root / "voxl_chromium/upstream.json").write_text("{invalid")
    with pytest.raises(json.JSONDecodeError):
        packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert calls == []
    assert len(list(output.iterdir())) == 1


def repin(root, archive, pin):
    pin["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (root / "voxl_chromium/upstream.json").write_text(json.dumps(pin))


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-commit",
        "traversal",
        "symlink",
        "hardlink",
        "directory",
        "oversize",
    ],
)
def test_unexpected_lock_member_produces_no_derived_outputs(bundle, kind):
    root, output, archive, pin, _ = bundle
    name = f"voxl-{UPSTREAM_COMMIT}/Cargo.lock"
    if kind == "wrong-commit":
        name = f"voxl-{'b' * 40}/Cargo.lock"
    elif kind == "traversal":
        name = f"voxl-{UPSTREAM_COMMIT}/../Cargo.lock"
    member = tarfile.TarInfo(name)
    member.type = {
        "symlink": tarfile.SYMTYPE,
        "hardlink": tarfile.LNKTYPE,
        "directory": tarfile.DIRTYPE,
    }.get(kind, tarfile.REGTYPE)
    member.linkname = "../../escape"
    data = b"x" * (2 * 1024 * 1024 + 1) if kind == "oversize" else ORIGINAL_LOCK
    if member.isfile():
        member.size = len(data)
    with tarfile.open(archive, "w:gz") as source:
        source.addfile(member, io.BytesIO(data) if member.isfile() else None)
    repin(root, archive, pin)
    error = KeyError if kind in {"wrong-commit", "traversal"} else SystemExit
    with pytest.raises(error, match="Cargo.lock"):
        packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert {path.name for path in output.iterdir()} == {
        f"ha-voxl-chromium-{VERSION}-source.tar.gz",
        f"voxl-{UPSTREAM_COMMIT}-source.tar.gz",
    }
    assert not (output.parent / "escape").exists()


def test_only_known_regular_lock_is_read_without_extracting_other_members(
    bundle,
    monkeypatch,
):
    root, output, archive, pin, _ = bundle
    with tarfile.open(archive, "w:gz") as source:
        for name in ("../../escape", f"voxl-{UPSTREAM_COMMIT}/Cargo.lock"):
            member = tarfile.TarInfo(name)
            member.size = len(ORIGINAL_LOCK)
            source.addfile(member, io.BytesIO(ORIGINAL_LOCK))
    repin(root, archive, pin)

    def forbidden(*args, **kwargs):
        pytest.fail("Packaging must not extract archive members to disk")

    monkeypatch.setattr(tarfile.TarFile, "extract", forbidden)
    monkeypatch.setattr(tarfile.TarFile, "extractall", forbidden)
    packager().package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    assert (output / "upstream-Cargo.lock").read_bytes() == ORIGINAL_LOCK
    assert not (output.parent / "escape").exists()
    assert not (output / f"voxl-{UPSTREAM_COMMIT}").exists()


def test_modified_local_patch_changes_manifest_not_original_lock_or_notice(bundle):
    root, output, _, _, _ = bundle
    module = packager()
    module.package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    before = json.loads((output / "source-manifest.json").read_text())
    patch = next((root / "voxl_chromium/patches").glob("*.patch"))
    patch.write_bytes(patch.read_bytes() + b"\n# effective-lock fixture modification\n")
    module.package_sources(VERSION, PROJECT_COMMIT, root=root, output=output)
    after = json.loads((output / "source-manifest.json").read_text())
    key = str(patch.relative_to(root))
    assert after["patches"][key] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert after["patches"][key] != before["patches"][key]
    assert (output / "upstream-Cargo.lock").read_bytes() == ORIGINAL_LOCK
    assert (output / "SOURCE-NOTICE.txt").read_text() == NOTICE


def test_default_output_is_repo_dist(bundle):
    root, output, _, _, _ = bundle
    dist = root / "dist"
    output.rename(dist)
    packager().package_sources(VERSION, PROJECT_COMMIT, root=root)
    assert (dist / "SOURCE-NOTICE.txt").read_text() == NOTICE
