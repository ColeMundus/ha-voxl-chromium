# Changelog

## 0.4.0

- Add native Home Assistant click, literal Unicode text, key-combination and
  directional scroll actions for the shared Chromium desktop.
- Serialize browser input, validate coordinates against the current desktop,
  and keep typed text out of process arguments and application logs.
- Keep the existing opt-in authentication and internal-only control boundary;
  no additional privileges or published ports are needed.

## 0.3.0

- Keep the streamed desktop running when the last Chromium window is closed.
  Restore Browser and Open URL can relaunch Chromium with its existing profile.
- Add **Open / restore Chromium** to the desktop's right-click menu, available
  even when optional Home Assistant automation is disabled.
- Add opt-in **Home Assistant actions** setup: generate a persistent private
  credential and advertise the companion integration through Supervisor discovery.
  Confirm the discovered integration instead of copying hostnames and tokens.
- Support companion installation through HACS using this same repository. HACS
  installation and a Core restart remain separate from installing the add-on.
- Preserve existing manual credentials, configuration entries and script IDs.
- Document the verified Home Assistant 2026.10.0 minimum and distinguish completed
  installed upgrades/backup creation from untested live backup restoration.
- Add README branding, concise compatibility/install badges and real HACS/hassfest
  validation. Update reviewed GitHub Actions and the Rust builder to 1.99.0.

## 0.2.1

- Bundle local brand images with the companion integration so Home Assistant
  displays its icon instead of the missing-icon placeholder.
- Use Chromium's managed `CommandLineFlagSecurityWarningsEnabled` policy to
  suppress the recurring command-line flag infobar in this dedicated desktop.
  This is a presentation change: Chromium still runs without its sandbox and
  no container privileges are added. The documented security limitation remains.

## 0.2.0

- Add an optional authenticated automation API on the private add-on network;
  it is disabled until an automation token is configured, with no host port published.
- Add a companion Home Assistant integration with native actions for opening URLs,
  restoring Chromium, taking screenshots, recording the desktop and waiting for
  screen changes or visual idle.
- Add an image entity for the most recently requested screenshot.
- Keep automation separate from interactive Ingress sessions and keep the control
  token out of Chromium's environment and command line.
- See the [automation setup guide](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/AUTOMATION.md).

## 0.1.1

- Add Chromium artwork to the add-on listing and details page, with its license.
- Use the browser icon for the Home Assistant sidebar entry.
- Fix documentation links that did not work inside Home Assistant.
- Keep browser settings, persistent data and streaming behavior unchanged.

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
