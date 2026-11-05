# Configuration and operation

## Requirements and startup

Use Home Assistant OS/Supervisor. Add `https://github.com/ColeMundus/ha-voxl-chromium` to the Add-on Store repositories, install **Voxl Chromium**, and start it. Standalone Home Assistant Core and Container cannot install add-ons.

Supervisor builds the add-on locally from its Dockerfile; there is no project-published image to pull. Allow time and disk space for compilation. Open **Web UI** through Home Assistant Ingress rather than connecting to an internal port.

Your **viewing browser** needs WebCodecs with H.264 decoding and a secure context: use Home Assistant over **HTTPS**. The localhost exception applies only when the viewing browser actually opens localhost; `http://192.168.x.x` and ordinary LAN HTTP hostnames do not qualify. A successful HTTP Ingress page load alone does not prove that video decoding is available.

This add-on is experimental. See [test coverage and known limitations](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/TESTING.md).

## Options

Set these on the add-on's **Configuration** tab, save, then restart the add-on. Defaults:

```yaml
start_url: https://www.home-assistant.io
width: 1280
height: 720
fps: 30
bitrate_kbps: 8000
audio: true
resize: true
resize_scale: 1.0
timezone: Etc/UTC
language: en-US
automation_token: ""
```

| Option | Meaning |
| --- | --- |
| `start_url` | Initial page opened in Chromium. Do not embed passwords or tokens in the URL. |
| `width`, `height` | Initial and maximum virtual desktop size in pixels. Automatic resize can shrink it within these limits. Larger desktops increase encoding CPU and memory use. |
| `fps` | Target maximum video frame rate; actual rate depends on load, changed content, and network conditions. |
| `bitrate_kbps` | Video bitrate ceiling in kilobits per second, not a guarantee of constant bitrate or achieved quality. |
| `audio` | Enable streamed browser audio. The viewer may require a click before allowing playback. |
| `resize` | Allow the streamed desktop to resize to the viewing window. Concurrent viewers share one desktop and can affect its size. |
| `resize_scale` | Scale used for viewer-requested resizing. Start at `1.0`; adjust conservatively if the desktop is too large or small. |
| `timezone` | IANA time zone name for the container/browser environment, for example `Etc/UTC` or `America/Chicago`. |
| `language` | Chromium language preference, default `en-US`; websites may also apply their own account/location preferences. |
| `automation_token` | Empty disables the automation API. Set a unique random token of at least 32 characters to enable authenticated control on private container port 8100. Never publish this port. See the [automation guide](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/AUTOMATION.md). |

The add-on configuration schema is the authority for accepted ranges and types. Reducing `width`, `height`, `fps`, and `bitrate_kbps` can help on slow hardware or connections. With `resize: true`, the desktop follows the viewer up to the configured initial dimensions; disable resizing when you need a fixed desktop size.

## Persistence, backups, and updates

- `/data/profile` holds the Chromium profile, including cookies, history, site data, and any stored credentials. Chromium uses `--password-store=basic`, without an OS keyring-backed secret store; do not assume saved passwords are securely encrypted at rest.
- `/data/Downloads` holds browser downloads. This is private add-on storage, not the Home Assistant host's Downloads directory or a mounted shared folder.
- Ordinary restarts retain add-on data. Include this add-on's data in Home Assistant backups, protect those backups, and test restore before relying on them. Live backup/restore and upgrade behavior have not yet been verified.
- Removing the add-on or deleting its data can destroy both directories. An update should not require deleting a profile. Stop the add-on and back up its data before attempting profile recovery.
- Treat the desktop as shared by all authorized viewers, not as separate profiles or sessions per Home Assistant user. Do not assume Chromium logins are restricted to the person who entered them.

Upstream-update PRs and Dependabot changes are reviewed, not automatically merged. Weekly CI builds test ephemeral containers; they do not publish/retag images or change your installed add-on. Apply reviewed source updates through the add-on update process, allowing time for a local rebuild. Source distribution is not a claim of full corresponding-source delivery for an exported binary image; see the [licensing notices](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/NOTICE.md) before sharing a local build.

## Security limitations

Interactive viewing uses Supervisor Ingress. nginx allows only the connecting Supervisor address `172.30.32.2`; voxl binds to loopback with its own authentication disabled. There are **no published ports**. Optional automation uses a separate bearer-authenticated API on the private add-on network, disabled when `automation_token` is empty. It does not bypass or change Ingress authentication. Do not expose either internal service, use host networking, or remove the allowlist to work around an access problem.

Chromium runs as UID 1000 but **without Chromium's sandbox**, because the container's Docker restrictions prevent the normal sandbox setup. This materially weakens protection against malicious pages and browser exploits; a non-root account and a container do not replace the browser sandbox. An exploit can threaten browser sessions, downloads, and reachable network services, and further flaws may allow container escape.

The dedicated desktop suppresses Chromium's recurring command-line warning
infobar using the managed `CommandLineFlagSecurityWarningsEnabled` policy. This
only changes presentation; it does not enable the sandbox or improve isolation.
No broad `--test-type` or `--disable-web-security` switches are added.

No Home Assistant/Supervisor API access/token or host-directory mounts are requested. This does not block network access to your LAN or prevent credentials you type into Chromium from being stored in its profile. Limit access and use appropriately trusted browsing workloads. The [security policy](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/SECURITY.md) explains private reporting.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Build is slow or fails | Inspect Supervisor build logs, available disk/memory, and network access to source/package servers. Compilation on ARM can take time. Include the failed build step and architecture in a sanitized issue. |
| Page opens but stream is blank | Confirm the Home Assistant URL is HTTPS (or truly localhost), WebCodecs is available in the viewing browser, and H.264 decoding is supported. Try a current Chromium-based viewer. Inspect browser-console errors without sharing session tokens. |
| 403 or cannot open directly | Use **Open Web UI** through Ingress. Direct connections are intentionally rejected. Do not weaken the allowlist or publish ports. If genuine Ingress fails, include Supervisor/network details and sanitized nginx logs. |
| Disconnections or high latency | Verify a reverse proxy forwards WebSockets and supports long-lived connections; test the authenticated Home Assistant URL without an extra proxy layer if available. Lower resolution/FPS/bitrate and check host CPU/memory. |
| No audio | Check `audio: true`, viewer tab/site mute settings, and autoplay permission. Click the page/player if the viewer requires user interaction. |
| Desktop keeps changing size | Multiple viewers share the desktop. Try `resize: false` for a fixed `width` and `height`. |
| Settings appear ignored | Save and restart the add-on; distinguish initial dimensions/start page from a persistent browser session and subsequent viewer resizing. |
| Profile will not open after an interrupted run | Stop the add-on, preserve a backup, and inspect logs. Do not delete the profile as a first step or post its contents publicly. |

For an issue include the add-on version/commit, architecture, Home Assistant/Supervisor versions, viewing browser/version, URL scheme (not a secret URL), relevant options with secrets removed, and sanitized logs. Never attach browser profiles, cookies, credentials, or Ingress session paths.
