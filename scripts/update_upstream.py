"""Update the reviewed upstream source pin; never bump the add-on version."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import URLError
from urllib.request import Request, urlopen

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024


class GitHubClient:
    """Use GitHub's release metadata; hash the exact Docker download URL."""

    def __init__(self, token: str | None = None):
        self.token = token

    def get_json(self, endpoint: str) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ha-voxl-chromium-updater",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            f"https://api.github.com/repos/tembo/voxl/{endpoint}", headers=headers
        )
        with urlopen(request, timeout=60) as response:
            payload = response.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise ValueError("GitHub JSON response is too large")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Expected a GitHub JSON object")
        return data

    def archive_sha256(self, commit: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid git object SHA")
        # Do not send credentials to GitHub archives or codeload redirects.
        request = Request(
            f"https://github.com/tembo/voxl/archive/{commit}.tar.gz",
            headers={"User-Agent": "ha-voxl-chromium-updater"},
        )
        digest = hashlib.sha256()
        size = 0
        with urlopen(request, timeout=60) as response:
            first = response.read(2)
            if first != b"\x1f\x8b":
                raise ValueError("Expected a gzip source archive")
            digest.update(first)
            size += len(first)
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("Upstream source archive is too large")
                digest.update(chunk)
        return digest.hexdigest()


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(
        r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError(f"Unsupported stable version: {value!r}")
    return tuple(map(int, value.removeprefix("v").split(".")))


def update(pin: Path, client) -> bool:
    current = json.loads(pin.read_text())
    release = client.get_json("releases/latest")
    if release["draft"] or release["prerelease"]:
        return False
    tag = release["tag_name"]
    if version_tuple(tag) <= version_tuple(current["version"]):
        return False
    obj = client.get_json(f"git/ref/tags/{tag}")["object"]
    seen = set()
    while True:
        commit = obj.get("sha", "")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid git object SHA")
        if obj.get("type") == "commit":
            break
        if obj.get("type") != "tag":
            raise ValueError("Git object is not a commit or annotated tag")
        if commit in seen or len(seen) >= 8:
            raise ValueError("Cyclic or excessively nested annotated tag")
        seen.add(commit)
        obj = client.get_json(f"git/tags/{commit}")["object"]
    digest = client.archive_sha256(commit)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid archive SHA-256")
    current.update(version=tag.removeprefix("v"), commit=commit, sha256=digest)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", dir=pin.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(current, indent=2) + "\n")
        temporary.chmod(pin.stat().st_mode & 0o777)
        os.replace(temporary, pin)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pin",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voxl_chromium" / "upstream.json",
    )
    args = parser.parse_args(argv)
    try:
        changed = update(args.pin, GitHubClient(token=os.environ.get("GITHUB_TOKEN")))
    except (OSError, URLError, ValueError, KeyError, TypeError) as exc:
        print(f"Upstream update failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(
        "Updated upstream source pin" if changed else "Upstream pin is already current"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
