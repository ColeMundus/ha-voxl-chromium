# voxl Chromium

**Version 0.1.0 · Experimental**

A persistent Chromium browser streamed with [voxl](https://github.com/tembo/voxl) through Home Assistant Supervisor Ingress. Intended architectures: `amd64` and `aarch64`; see release validation notes rather than assuming both are runtime-tested. Live Home Assistant operation has not yet been verified.

## Install and open

Requires Home Assistant Supervisor (normally Home Assistant OS); standalone Core and Container installations cannot install add-ons.

Add `https://github.com/ColeMundus/ha-voxl-chromium` under **Settings → Add-ons → Add-on Store → ⋮ → Repositories**. Install **Voxl Chromium**, allow Supervisor to build it locally, start it, then select **Open Web UI**. The repository is public; this experimental version has not yet been tested on a live Supervisor installation.

There are no project-published prebuilt images. Initial releases contain add-on source and the exact voxl source; a local build may be slow on small systems. The viewing browser needs **HTTPS and WebCodecs/H.264 decoding** (localhost is the secure-context exception).

## Read before use

- Profile and downloads persist in `/data/profile` and `/data/Downloads`.
- Ingress-only by default, no published ports; nginx allows only Supervisor `172.30.32.2`.
- voxl is loopback-only with its own authentication disabled; do not expose it directly.
- Chromium runs as UID 1000 **without its sandbox** due to Docker restrictions. A page/browser compromise can expose stored sessions and add-on data; container isolation is not an equivalent sandbox.
- No Home Assistant/Supervisor API token/access or host-directory mounts are requested. The browser can still reach network services and retain credentials you enter.

See [DOCS.md](DOCS.md) for options, persistence, and troubleshooting; the repository's [security policy](../SECURITY.md) and [licensing/source-delivery notice](../NOTICE.md) describe the wider boundaries. Original project code/docs are GPL-2.0-or-later. voxl source remains MIT, its x264-linked binary is GPL-covered, and independent Debian programs retain their own licenses.
