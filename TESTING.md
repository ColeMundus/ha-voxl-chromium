# Testing

## Automated coverage

Run the unit/regression suite with `uv run pytest`. Native amd64 and aarch64 CI
also builds the image and exercises it through an Ingress-like test proxy:

- Health, relative assets, WebSocket upgrades and actual H.264 WebCodecs decoding.
- Mouse/keyboard input and PulseAudio/Opus transport.
- File upload/download, directory ZIP downloads and traversal rejection.
- Non-root browser identity and removal of inherited Supervisor credentials.
- Browser site-data/download persistence after restart, dynamic resolution and
  full-container shutdown when a core service fails.

CI runs upstream voxl library tests during the build, Ruff, the Home Assistant
metadata linter, and image/effective-Rust-lockfile vulnerability scans. Scans use
`HIGH,CRITICAL` and `--ignore-unfixed`: a passing result is not proof that no
vulnerabilities exist. The security patch is documented in
[MODIFICATIONS.md](voxl_chromium/MODIFICATIONS.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the exact development commands.

## Verification status

- [Complete native amd64/aarch64 CI](https://github.com/ColeMundus/ha-voxl-chromium/actions/runs/41756376904)
  passed builds, browser tests and both security gates.
- The maintainer reports successful Home Assistant installation and streaming.
  This is live user confirmation, distinct from the automated proxy fixture.

- Live Supervisor backup/restore, installed-version upgrades and release publication
  have not yet been verified.

Current and historical results are available in the repository's
[Actions tab](https://github.com/ColeMundus/ha-voxl-chromium/actions).
