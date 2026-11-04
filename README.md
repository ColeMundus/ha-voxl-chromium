# voxl Chromium for Home Assistant

**0.1.0 · Experimental** — a persistent Chromium browser streamed through Home Assistant Ingress with [voxl](https://github.com/tembo/voxl).

## Install

Requires **Home Assistant Supervisor** (normally Home Assistant OS). Home Assistant Container and standalone Home Assistant Core cannot install Supervisor add-ons.

Install this experimental add-on from its public repository:

1. Open **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/ColeMundus/ha-voxl-chromium`.
3. Find **voxl Chromium**, install it, review its Configuration, and start it.
4. Select **Open Web UI** to connect through Ingress.

The initial release is **source-build-only**: Supervisor builds the add-on locally from `voxl_chromium/Dockerfile`. There is no prebuilt-image `image` key, GHCR publication, or image to pull from this project. Release packages are intended to contain the add-on source and the exact pinned voxl source. Building can take significant time and disk space, especially on small ARM systems; do not interrupt the build.

Open Home Assistant over **HTTPS**. The viewing browser needs WebCodecs and H.264 decoding in a secure context; ordinary `http://<LAN-IP>` does not qualify. Browsers also treat `localhost` as a secure-context exception. The browser inside the add-on does not remove this requirement for your viewing device.

## Defaults and storage

The browser opens `https://www.home-assistant.io` at 1280 × 720, up to 30 FPS and an 8000 kbit/s video ceiling, with audio and automatic resizing enabled. See [configuration and troubleshooting](voxl_chromium/DOCS.md) for every option.

- Chromium profile: `/data/profile`
- Downloads: `/data/Downloads`

These paths use the add-on's private persistent data volume, not host mounts. Treat the profile as sensitive: it can contain cookies, saved credentials, and browsing history. Include add-on data in backups and verify recovery before relying on it. Uninstalling the add-on or deleting its data can remove both paths.

## Security boundaries

- Ingress-only by default: **no published container ports**. nginx accepts requests only from Supervisor at `172.30.32.2` and rejects other source addresses.
- voxl listens on loopback with its own authentication **disabled**; Supervisor Ingress is the user-facing authentication boundary. Do not expose or forward internal service ports, remove the nginx allowlist, or enable host networking.
- Chromium runs as **UID 1000**, but **without Chromium's sandbox** because of the container's Docker restrictions. Non-root operation is not a replacement for the browser sandbox. A malicious page or browser exploit could compromise the add-on and its stored browser sessions; container isolation is not a guarantee against escape.
- No Home Assistant API access, Supervisor API access/token, or host-directory mounts are requested. The browser can still access network services reachable from the container; this is not a network isolation tool. A user can also manually enter Home Assistant credentials into Chromium, which creates ordinary browser sessions.

Use only with trusted users and appropriately trusted browsing workloads. See [SECURITY.md](SECURITY.md).

## Status and compatibility

`amd64` and `aarch64` are intended targets. Local image/smoke checks are not equivalent to running under Supervisor; **live Home Assistant installation, Ingress, backup/restore, and upgrade behavior have not been verified**. Do not infer tested support for either architecture from the intended target list. Release notes should identify the actual architecture and checks performed.

The initial development build was exercised on **Linux amd64** with real H.264
WebCodecs decoding, remote mouse/keyboard input, Opus audio transport, file transfer
and traversal rejection, UID/token checks, browser site-data persistence across
restart, dynamic resolution, and shutdown on service failure. The
[first GitHub CI run](https://github.com/ColeMundus/ha-voxl-chromium/actions/runs/41753324664)
also passed native **amd64 and aarch64 image builds and real-container smoke
tests**. That run failed later in the scanner's download installer, so it was
**not a fully green CI run**; the replacement pinned scanner container has been
locally tested but still needs a complete remote rerun. See [TESTING.md](TESTING.md)
for the distinction between passed checks and remaining verification.

H.264 encoding is CPU-based. Large desktops, high frame rates, and busy pages can consume substantial CPU and memory on a Home Assistant host. Start with the defaults and reduce resolution/FPS if needed. Audio playback also depends on the viewing browser's autoplay policy.

## Development

The image build context and Dockerfile are inside `voxl_chromium/`:

```sh
docker build -t ha-voxl-chromium:dev voxl_chromium
uv run pytest
uv run playwright install --with-deps chromium
uv run python tests/smoke.py --image ha-voxl-chromium:dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the review, security, licensing, and release checklist. Upstream-update PRs and Dependabot changes require review and are **not automatically merged**. Weekly scheduled CI is intended to rebuild and test ephemeral local containers, not publish or silently retag images. A passing scheduled build does not update an installed add-on; reviewed source updates must be installed and rebuilt.

Builds apply a documented [security dependency patch](voxl_chromium/MODIFICATIONS.md)
to upstream voxl and run its library tests. CI scans Debian packages and the
effective patched Rust lockfile separately; an OS-only scan cannot inspect all
statically linked Rust dependencies. Review the local patch when updating voxl.

## CI runners

Only the canonical repository's trusted `main` pushes, manual runs and schedules
route the amd64 container job to an on-demand Mu VM (12 vCPUs, 8 GiB RAM). PRs,
forks, automation branches and release tags stay on GitHub-hosted Ubuntu 24.04;
ARM always uses native `ubuntu-24.04-arm`. The controller polls every 20 seconds
and powers off the VM after 60 seconds of confirmed idle time, so an offline
runner between jobs is normal. There are no host home-directory/Docker mounts
or Internet-facing forwarded ports; maintenance SSH is loopback-only on 2227.
See [runner operations and trust boundaries](runner/README.md) for lifecycle,
workspace cleanup, serialization and public-workflow security caveats.

## License

Original project code and documentation are **GPL-2.0-or-later**; [LICENSE](LICENSE) contains the unmodified GNU GPL version 2 text. Upstream voxl source is MIT-licensed, but the voxl executable links GPL-covered x264. Independent programs in the Debian runtime, including Chromium, retain their own licenses; the image is not uniformly relicensed.

Read [NOTICE.md](NOTICE.md) for attribution and the source-delivery workflow required before distributing binaries. A license file, a Dockerfile, or an upstream link alone does **not** establish compliance. No warranty is provided.
