"""Package checksum-verified upstream source alongside the tracked project archive."""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_sources(
    version: str,
    commit: str,
    *,
    root: Path = ROOT,
    output: Path | None = None,
) -> None:
    """Complete the source bundle after the workflow's git archive step."""
    pin = json.loads((root / "voxl_chromium/upstream.json").read_text())
    upstream_commit, expected = pin["commit"], pin["sha256"]
    if not re.fullmatch(r"[0-9a-f]{40}", upstream_commit):
        raise SystemExit("Invalid upstream commit")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("Invalid upstream archive SHA-256")
    dist = output if output is not None else root / "dist"
    archive = dist / f"voxl-{upstream_commit}-source.tar.gz"
    subprocess.run(
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
            str(archive),
            f"https://github.com/tembo/voxl/archive/{upstream_commit}.tar.gz",
        ],
        check=True,
    )
    with archive.open("rb") as downloaded:
        if hashlib.file_digest(downloaded, "sha256").hexdigest() != expected:
            raise SystemExit("Upstream archive checksum mismatch")
    with tarfile.open(archive) as source:
        member = source.getmember(f"voxl-{upstream_commit}/Cargo.lock")
        if not member.isfile() or member.size > 2 * 1024 * 1024:
            raise SystemExit("Unexpected upstream Cargo.lock")
        (dist / "upstream-Cargo.lock").write_bytes(source.extractfile(member).read())
    (dist / "source-manifest.json").write_text(
        json.dumps(
            {
                "project_commit": commit,
                "project_version": version,
                "upstream": pin,
                "patches": {
                    str(path.relative_to(root)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in sorted((root / "voxl_chromium/patches").glob("*.patch"))
                },
                "distribution": "source-build-only",
            },
            indent=2,
        )
        + "\n"
    )
    (dist / "SOURCE-NOTICE.txt").write_text(
        "Source-build-only release. No container images or binaries are distributed.\n"
        "Includes tracked project source, the exact upstream archive, and Cargo.lock.\n"
        "The project source includes local patches; apply them before building.\n"
        "upstream-Cargo.lock is the ORIGINAL lock; "
        "local patches define the effective lock.\n"
        "Cargo.lock records dependency versions; dependency sources are not vendored.\n"
        "This convenience bundle does not claim to be complete corresponding source\n"
        "for Chromium, Debian, Rust dependencies, or any locally built binary image.\n"
        "Review dependency licenses and source-delivery obligations "
        "before redistribution.\n"
    )
    entries = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(dist.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (dist / "SHA256SUMS").write_text("".join(entries))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    package_sources(args.version, args.commit, root=args.root, output=args.output)


if __name__ == "__main__":
    main()
