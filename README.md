<img src="https://raw.githubusercontent.com/ColeMundus/ha-voxl-chromium/main/voxl_chromium/icon.png" alt="Chromium icon" width="80" height="80">

# Voxl Chromium for Home Assistant

[![CI](https://github.com/ColeMundus/ha-voxl-chromium/actions/workflows/ci.yml/badge.svg)](https://github.com/ColeMundus/ha-voxl-chromium/actions/workflows/ci.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-voxl-chromium&category=integration)
[![GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](LICENSE)

A persistent Chromium browser inside Home Assistant, streamed through
[voxl](https://github.com/tembo/voxl) and Supervisor Ingress.

- H.264 desktop streaming with mouse, keyboard, clipboard and audio.
- Persistent browser profile and downloads.
- Automatic desktop resizing and file transfers.
- Opens through Home Assistant; no additional published ports.
- Optional native Home Assistant automation actions: open URLs, restore Chromium,
  capture screenshots, record clips and wait for screen changes. Install the
  companion integration from this **same repository through HACS**, enable
  **Home Assistant actions** in the add-on, then confirm discovery—no hostname or
  token copying. See [automation setup](AUTOMATION.md).

## Install

Requires **Home Assistant OS/Supervisor**, an **amd64 or aarch64** host, and an
**HTTPS Home Assistant URL**. Home Assistant Container cannot install add-ons.

1. Open **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/ColeMundus/ha-voxl-chromium`.
3. Install **Voxl Chromium**, then select **Start → Open Web UI**.

[Add the add-on repository](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FColeMundus%2Fha-voxl-chromium)
· [Install the optional HACS integration](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-voxl-chromium&category=integration)

Supervisor builds the image locally. The first installation can take several
minutes, particularly on smaller ARM hosts. WebCodecs requires HTTPS; ordinary
LAN HTTP will not decode the stream (localhost is an exception).

## Configuration

The defaults provide a 1280 × 720 desktop, up to 30 FPS, audio and automatic
resizing. Profile data lives in `/data/profile`; downloads in `/data/Downloads`.
Both survive ordinary restarts.

See [configuration and troubleshooting](voxl_chromium/DOCS.md) for all options.

## Security

**Chromium runs as a non-root user with its browser sandbox disabled.** The
container does not replace that security layer. Use trusted users and browsing
workloads; all viewers share the same profile and signed-in sessions.

The add-on requests no broad Home Assistant/Supervisor API permissions or host mounts.
Ingress authenticates access, nginx accepts only Supervisor connections, and voxl
listens on loopback. Do not expose its internal ports. See [SECURITY.md](SECURITY.md).

## Development

This add-on is experimental. See [CONTRIBUTING.md](CONTRIBUTING.md) for development
and update procedures, and [TESTING.md](TESTING.md) for coverage and known limitations.

## License

Project code is **GPL-2.0-or-later**. Upstream voxl source is MIT; its executable
links GPL-covered x264. Other runtime components retain their own licenses.
Distribution is source-build-only. See [LICENSE](LICENSE), [NOTICE.md](NOTICE.md)
and the [local dependency patch](voxl_chromium/MODIFICATIONS.md).
