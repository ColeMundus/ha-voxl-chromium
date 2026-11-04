# Contributing

Keep changes focused, preserve the Ingress security boundary, and add tests for
changed behavior. Project contributions are GPL-2.0-or-later.

## Development checks

Use Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and Docker:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker build -t ha-voxl-chromium:dev voxl_chromium
uv run playwright install --with-deps --only-shell chromium
uv run python tests/smoke.py --image ha-voxl-chromium:dev
git diff --check
```

The image build context is `voxl_chromium/`, not the repository root. The smoke
suite creates a disposable `172.30.32.0/24` Docker network, containers and volume.
Run it only on a development daemon with no conflicting subnet, one test at a
time. Screenshots and logs go to ignored `artifacts/`; the fixture proxy is
never copied into the add-on image.

## Review checklist

- Keep configuration, runtime defaults, translations and option docs synchronized.
- Preserve loopback voxl, Supervisor-peer authorization, non-root operation and
  the absence of API permissions, exposed ports and host-directory mounts.
- Preserve browser profile/downloads and graceful shutdown. Do not log credentials,
  cookies or Ingress session paths.
- Review upstream source and dependency licenses; retain immutable checksums,
  exact local patches and locked builds.
- Run tests, update the changelog for user-visible changes, and report which
  architectures and live behavior were actually tested.

## Automation and releases

CI builds and tests both supported architectures and scans container packages
and the effective Rust lockfile. Review workflow changes carefully before approving
contributor runs; never execute untrusted PR code in a privileged job.

Dependabot and the weekly upstream check propose reviewed updates; nothing
auto-merges or silently upgrades installed add-ons. On forks, enable scheduled
workflows and allow Actions to create pull requests if using the upstream updater.

For a release, update `voxl_chromium/config.yaml` and its changelog, pass CI, then
push the matching `vX.Y.Z` tag with maintainer approval. The release workflow
validates the version and packages project source, exact upstream source, local
patches, provenance and checksums. Verify the resulting downloads before announcing.
Do not publish container images or binaries without the corresponding-source
process described in [NOTICE.md](NOTICE.md). Preserve historical releases.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
