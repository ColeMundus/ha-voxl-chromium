# Changelog

## 0.1.0 — Experimental initial source release

- Patch upstream's ZIP dependency to 9.0.5, preserve the effective lockfile, and
  scan Rust dependencies separately from operating-system packages.

- Introduces a Supervisor add-on for a persistent Chromium desktop streamed by voxl through Ingress.
- Supports `amd64` and `aarch64`, with native image-build and browser-smoke CI.
- Persists the profile in `/data/profile` and downloads in `/data/Downloads`.
- Defaults to no published ports, a Supervisor-only nginx allowlist, and a loopback-only voxl service with its own authentication disabled.
- Runs Chromium as UID 1000 without the Chromium sandbox due to Docker restrictions; this is a material security limitation, not equivalent protection.
- Uses source-only distribution with local Supervisor builds, not project-published binary images. Release packaging is expected to include exact pinned voxl source and notices.
- Documents GPL-2.0-or-later project licensing, upstream voxl's MIT license, x264-linked binary obligations, and separate runtime-component licenses.
- Maintenance policy: reviewed upstream-update/Dependabot PRs and weekly ephemeral CI builds; no auto-merges or silently retagged images.

This entry describes the initial release scope, not evidence that a release has been published or that every target has passed runtime testing.
