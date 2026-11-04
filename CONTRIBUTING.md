# Contributing

This is an experimental, source-build-only Supervisor add-on. Keep changes small, reproducible, and explicit about which checks actually ran. Do not publish images or releases as part of a contribution.

## Local checks

Use Docker, Python with [uv](https://docs.astral.sh/uv/), and a checkout of this repository:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker build -t ha-voxl-chromium:dev voxl_chromium
uv run playwright install --with-deps chromium
uv run python tests/smoke.py --image ha-voxl-chromium:dev
git diff --check
```

The Dockerfile and its build context are **inside `voxl_chromium/`**, not the repository root. Test images are local artifacts; do not push them to GHCR or another registry. Report architecture, commands, and results in your PR. Do not call a cross-build a native runtime test, or a Docker smoke check a live Home Assistant test.

The smoke harness creates and removes its own Docker containers, volume, and a
`172.30.32.0/24` bridge to emulate Supervisor's trusted source IP. Run only one
smoke test at a time, on a development Docker daemon without a conflicting subnet.
Its fixture proxy is test-only and is never copied into the add-on image.
Screenshots and sanitized container logs are saved under ignored `artifacts/`.

To enable automated update PRs after publication, enable **Settings → Actions →
General → Workflow permissions → Allow GitHub Actions to create and approve pull
requests**. The automation creates PRs, never approves or auto-merges them. On
forks, enable scheduled workflows explicitly if GitHub disables them by default.

## Change requirements

- Keep option defaults and validation synchronized with `voxl_chromium/DOCS.md` and the add-on configuration.
- Preserve Ingress-only access, nginx's Supervisor-only `172.30.32.2` allowlist, loopback voxl, and the absence of API/token permissions and host mounts. Treat changes to those boundaries as security-sensitive.
- Keep the browser non-root (UID 1000). Document that it runs without Chromium's sandbox; never advertise Docker as equivalent protection.
- Preserve `/data/profile` and `/data/Downloads` across ordinary restarts and upgrades. Test backup/restore under Supervisor before claiming it works.
- Pin upstream voxl to an immutable revision with a verified source checksum. Review its changes, command-line flags, dependencies, and licensing before accepting an update.
- Use tests for changed behavior and update the changelog. Avoid logging credentials, cookies, session URLs, or profile contents.
- Contributions to this project's original code/docs are under GPL-2.0-or-later. Preserve third-party licenses and attribution; do not copy code without checking its terms.

## Automated maintenance

Upstream voxl update PRs and Dependabot updates are proposals for human review, **not automatic merges**. Weekly scheduled CI rebuilds and tests ephemeral containers to detect breakage; it does not publish images, silently retag a release, or upgrade installed add-ons. Review changed source/dependency licenses, build logs, security impact, and actual test results before merging. Do not turn an automation token into a runtime add-on credential.

## Source-release checklist

1. Pass configuration/tests, image build, and smoke checks; record the tested architecture(s) and any limitations.
2. Where available, separately test installation, HTTPS Ingress/WebSocket streaming, restart persistence, add-on backup/restore, and upgrade behavior on a real Supervisor installation. Mark any untested items explicitly.
3. Check `version` and changelog entries, option docs, security notices, and all distribution artifacts. The initial version is `0.1.0`, experimental.
4. Assemble the add-on source **and exact pinned voxl source**, licenses, checksums, and local changes for release. Validate the packaged content and download checksums. A generated repository archive alone may omit the downloaded upstream source.
5. Keep the release source-only: no add-on prebuilt-image `image` key, container push, registry release, or downloadable binary image artifact. Do not claim the source package is a complete corresponding-source bundle for locally produced images. Follow [NOTICE.md](NOTICE.md) before any future binary distribution.
6. Publish only after explicit maintainer approval. Do not claim the planned repository URL is already online or installation is live-tested without evidence.

Report bugs with the issue template and sanitized logs. Use [SECURITY.md](SECURITY.md) instead of a public issue for vulnerabilities.
