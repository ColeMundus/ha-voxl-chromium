# Voxl Chromium

A persistent Chromium browser streamed through Home Assistant Ingress with
[voxl](https://github.com/tembo/voxl). Supports amd64 and aarch64.

Install, start, then select **Open Web UI**. Home Assistant must use **HTTPS**
for the viewing browser's WebCodecs decoder. Supervisor builds the image locally;
the first installation may take time.

The browser profile and downloads persist across restarts. All viewers share the
same browser sessions. **Chromium's sandbox is disabled**; use trusted users and
browsing workloads. No additional ports or Home Assistant API permissions are needed.

See [configuration and troubleshooting](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/voxl_chromium/DOCS.md),
the [security policy](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/SECURITY.md)
and [licensing notices](https://github.com/ColeMundus/ha-voxl-chromium/blob/main/NOTICE.md).
This add-on is experimental.
