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

- Authenticated automation API over the private container network, bearer/Origin
  rejection, new/current-tab URL navigation and actual minimize/restore state.
- Closing the last Chromium window without stopping the desktop/API, relaunch
  through both native actions and the local restore helper with automation off.
- PNG dimensions/pixels, visual idle/change waits, short MP4 capture, token
  exclusion from Chromium and disabled-listener behavior with an empty token.

The separate `integration_tests/` project runs the real Home Assistant 2026.10.4
flow, action and image-entity harness. It tests authentication, reconfiguration,
multi-entry unloading, bounded media transport and allowlisted no-overwrite
exports. Run `uv run --project integration_tests pytest integration_tests`.

CI runs upstream voxl library tests during the build, Ruff, the add-on linter,
hassfest, HACS validation and image/effective-Rust-lockfile vulnerability scans. Scans use
`HIGH,CRITICAL` and `--ignore-unfixed`: a passing result is not proof that no
vulnerabilities exist. The security patch is documented in
[MODIFICATIONS.md](voxl_chromium/MODIFICATIONS.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the exact development commands.

## Verification status

- [Version 0.2.1 native amd64/aarch64 CI](https://github.com/ColeMundus/ha-voxl-chromium/actions/runs/42115960910)
  passed builds, browser tests and both security gates.
- Add-on upgrades from 0.1.1 → 0.2.0 → 0.2.1, backup creation, companion
  integration installation, restore-window/wait/screenshot actions and bundled
  branding were checked on a live Supervisor host with Core **2026.10.0**.
- The full integration suite, including Supervisor discovery, passes against
  actual Core **2026.10.0** and the default **2026.10.4** development harness.
- **Backup creation is not restoration.** Live Supervisor backup restoration is
  not verified. Restart/profile persistence is covered by disposable containers;
  a future destructive restore exercise should use a disposable Supervisor host,
  not the working household installation.
- Version 0.3.0 browser recovery passes the disposable-container smoke test;
  discovery is covered by the real Core flow harness and Supervisor API fixtures.
  That is not a claim of a fresh HACS installation or live Supervisor discovery.

Current and historical results are available in the repository's
[Actions tab](https://github.com/ColeMundus/ha-voxl-chromium/actions).
