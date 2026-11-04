"""Offline regression tests for the upstream pin updater."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_upstream.py"


def load_updater():
    assert SCRIPT.is_file(), "the upstream updater must exist"
    spec = importlib.util.spec_from_file_location("update_upstream", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, version="v0.4.0"):
        self.version = version
        self.requests = []
        self.commit = "b" * 40

    def get_json(self, endpoint):
        self.requests.append(endpoint)
        if endpoint == "releases/latest":
            return {"tag_name": self.version, "draft": False, "prerelease": False}
        if endpoint == f"git/ref/tags/{self.version}":
            return {"object": {"type": "commit", "sha": self.commit}}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    def archive_sha256(self, commit):
        assert commit == self.commit
        self.requests.append(f"archive/{commit}")
        return "c" * 64


def make_pin(tmp_path):
    pin = tmp_path / "upstream.json"
    pin.write_text(
        json.dumps({"version": "0.3.2", "commit": "a" * 40, "sha256": "d" * 64}) + "\n"
    )
    return pin


def test_new_release_updates_immutable_pin(tmp_path):
    updater = load_updater()
    pin = make_pin(tmp_path)
    client = FakeClient()
    assert updater.update(pin, client) is True
    assert json.loads(pin.read_text()) == {
        "version": "0.4.0",
        "commit": "b" * 40,
        "sha256": "c" * 64,
    }
    assert client.requests == [
        "releases/latest",
        "git/ref/tags/v0.4.0",
        f"archive/{'b' * 40}",
    ]


@pytest.mark.parametrize("tag", ["v0.3.2", "v0.3.1", "v0.2.99"])
def test_same_or_older_release_leaves_file_byte_identical(tmp_path, tag):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()
    client = FakeClient(tag)
    assert updater.update(pin, client) is False
    assert pin.read_bytes() == before
    assert client.requests == ["releases/latest"]


def test_numeric_versions_are_not_compared_lexically(tmp_path):
    updater = load_updater()
    pin = make_pin(tmp_path)
    assert updater.update(pin, FakeClient("v0.10.0")) is True
    assert json.loads(pin.read_text())["version"] == "0.10.0"


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_unstable_release_does_not_change_pin(tmp_path, flag):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()

    class UnstableClient(FakeClient):
        def get_json(self, endpoint):
            release = super().get_json(endpoint)
            release[flag] = True
            return release

    assert updater.update(pin, UnstableClient()) is False
    assert pin.read_bytes() == before


@pytest.mark.parametrize(
    "tag", ["v0.4.0-rc.1", "v0.4.0+meta", "v01.4.0", "../bad", "v1.2", "1.2.3\n"]
)
def test_invalid_release_version_fails_closed(tmp_path, tag):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()
    with pytest.raises(ValueError, match="version"):
        updater.update(pin, FakeClient(tag))
    assert pin.read_bytes() == before


@pytest.mark.parametrize("depth", [1, 3])
def test_annotated_tags_are_peeled_to_immutable_commit(tmp_path, depth):
    updater = load_updater()
    pin = make_pin(tmp_path)

    class AnnotatedClient(FakeClient):
        def get_json(self, endpoint):
            if endpoint == f"git/ref/tags/{self.version}":
                return {"object": {"type": "tag", "sha": f"{1:040x}"}}
            if endpoint.startswith("git/tags/"):
                level = int(endpoint.rsplit("/", 1)[1], 16)
                if level < depth:
                    return {"object": {"type": "tag", "sha": f"{level + 1:040x}"}}
                return {"object": {"type": "commit", "sha": self.commit}}
            return super().get_json(endpoint)

    assert updater.update(pin, AnnotatedClient()) is True
    assert json.loads(pin.read_text())["commit"] == "b" * 40


@pytest.mark.parametrize(
    "obj",
    [
        {"type": "commit", "sha": "bad"},
        {"type": "tree", "sha": "b" * 40},
        {"type": "tag", "sha": "../unsafe"},
    ],
)
def test_invalid_git_object_is_rejected_without_writing(tmp_path, obj):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()

    class BadObjectClient(FakeClient):
        def get_json(self, endpoint):
            if endpoint.startswith("git/"):
                return {"object": obj}
            return super().get_json(endpoint)

    with pytest.raises(ValueError, match="object"):
        updater.update(pin, BadObjectClient())
    assert pin.read_bytes() == before


def test_cyclic_annotated_tag_is_rejected_without_writing(tmp_path):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()

    class CyclicClient(FakeClient):
        def get_json(self, endpoint):
            if endpoint.startswith("git/"):
                return {"object": {"type": "tag", "sha": "b" * 40}}
            return super().get_json(endpoint)

    with pytest.raises(ValueError, match="tag"):
        updater.update(pin, CyclicClient())
    assert pin.read_bytes() == before


def test_transport_authenticates_api_but_never_archive(monkeypatch):
    updater = load_updater()
    archive = b"\x1f\x8b" + b"source archive" * 100000
    requests = []

    def open_request(request, *, timeout):
        assert timeout == 60
        requests.append(request)
        if request.full_url.startswith("https://api.github.com/"):
            return io.BytesIO(b'{"tag_name": "v0.4.0"}')
        return io.BytesIO(archive)

    monkeypatch.setattr(updater, "urlopen", open_request)
    client = updater.GitHubClient(token="test-token")
    assert client.get_json("releases/latest") == {"tag_name": "v0.4.0"}
    assert client.archive_sha256("b" * 40) == hashlib.sha256(archive).hexdigest()
    assert (
        requests[0].full_url
        == "https://api.github.com/repos/tembo/voxl/releases/latest"
    )
    assert requests[0].get_header("Authorization") == "Bearer test-token"
    assert requests[1].full_url == (
        f"https://github.com/tembo/voxl/archive/{'b' * 40}.tar.gz"
    )
    assert requests[1].get_header("Authorization") is None


@pytest.mark.parametrize("payload", [b"", b"<html>server error</html>"])
def test_transport_rejects_non_archive_response(monkeypatch, payload):
    updater = load_updater()
    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: io.BytesIO(payload))
    with pytest.raises(ValueError, match="archive"):
        updater.GitHubClient().archive_sha256("b" * 40)


def test_transport_bounds_archive_download(monkeypatch):
    updater = load_updater()
    monkeypatch.setattr(updater, "MAX_ARCHIVE_BYTES", 4)
    monkeypatch.setattr(updater, "urlopen", lambda *a, **k: io.BytesIO(b"\x1f\x8b123"))
    with pytest.raises(ValueError, match="large"):
        updater.GitHubClient().archive_sha256("b" * 40)


def test_archive_download_failure_preserves_pin_and_addon_version(tmp_path):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()
    config = tmp_path / "config.yaml"
    config.write_text('version: "0.1.0"\n')

    class FailingClient(FakeClient):
        def archive_sha256(self, commit):
            raise URLError("connection interrupted")

    with pytest.raises(URLError):
        updater.update(pin, FailingClient())
    assert pin.read_bytes() == before
    assert config.read_text() == 'version: "0.1.0"\n'


def test_cli_no_update_reports_unchanged(tmp_path, capsys, monkeypatch):
    updater = load_updater()
    pin = make_pin(tmp_path)
    monkeypatch.setattr(updater, "GitHubClient", lambda **k: FakeClient("v0.3.2"))
    assert updater.main(["--pin", str(pin)]) == 0
    assert "already current" in capsys.readouterr().out


def test_cli_error_is_nonzero_without_secret_traceback(tmp_path, capsys, monkeypatch):
    updater = load_updater()
    pin = make_pin(tmp_path)

    class FailingClient(FakeClient):
        def get_json(self, endpoint):
            raise URLError("request failed")

    monkeypatch.setattr(updater, "GitHubClient", lambda **k: FailingClient())
    assert updater.main(["--pin", str(pin)]) == 1
    output = capsys.readouterr()
    assert "Upstream update failed" in output.err
    assert "Traceback" not in output.err


def test_failed_atomic_replace_preserves_original_pin(tmp_path, monkeypatch):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(updater.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        updater.update(pin, FakeClient())
    assert pin.read_bytes() == before
    assert list(tmp_path.iterdir()) == [pin]


def test_invalid_archive_hash_preserves_original_pin(tmp_path):
    updater = load_updater()
    pin = make_pin(tmp_path)
    before = pin.read_bytes()

    class InvalidHashClient(FakeClient):
        def archive_sha256(self, commit):
            return "not-a-sha256"

    with pytest.raises(ValueError, match="SHA-256"):
        updater.update(pin, InvalidHashClient())
    assert pin.read_bytes() == before
