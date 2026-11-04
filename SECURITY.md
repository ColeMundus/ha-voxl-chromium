# Security policy

## Status and threat model

Version `0.1.0` is experimental. There is no security-support SLA or claim of a completed security audit. Use reviewed current source; upstream update PRs and weekly CI are detection/review mechanisms, not a guarantee that vulnerabilities are fixed. The initial distribution is source-build-only, with no project-published container image.

Anyone who can access the browser through Ingress can interact with its shared profile, sessions, clipboard, downloads, and reachable network services. Do not treat it as a private, per-user desktop or a safe place for untrusted users to browse arbitrary sites.

## Intended boundaries

- Supervisor Ingress handles user-facing access; nginx accepts connections only from `172.30.32.2`. No ports are published by default.
- voxl binds to loopback and has its own authentication **disabled**. Exposing it directly or removing the nginx source allowlist bypasses the intended boundary. A forwarded address header is not a replacement for checking the connecting peer.
- Chromium runs as UID 1000 **with its sandbox disabled because of Docker restrictions**. This removes a significant browser security layer. Non-root execution and container isolation do not replace it. A compromised page/browser may access the browser profile and downloads, attack services reachable over the network, or exploit further vulnerabilities to escape the container.
- The add-on requests no Home Assistant/Supervisor API access or runtime API token, and no host-directory mounts. This does not prevent a user from signing into Home Assistant or other services in Chromium, storing secrets in the profile, or exposing them to someone controlling that browser.
- `/data/profile`, `/data/Downloads`, and backups may contain sensitive data. Chromium uses `--password-store=basic`, not an OS keyring-backed secret store; do not assume saved credentials are securely encrypted at rest. Protect access and encrypt backups where appropriate. Do not attach profile archives to issues.
- Use HTTPS for Home Assistant access. WebCodecs requires a secure context (except the browser's localhost exception); a LAN HTTP connection is not an equivalent secure deployment.

Do not enable host networking, publish internal ports, mount host paths, or relax protection settings as a workaround for a failed stream. Review such changes as a different, higher-risk deployment.

## Reporting a vulnerability

Do **not** publish exploit details, session URLs, cookies, passwords, or unredacted logs in an issue.

Prefer GitHub's **Security → Advisories → Report a vulnerability** for `ColeMundus/ha-voxl-chromium` when the private-reporting option is available. If that private channel is unavailable, open only a minimal public request for a private contact channel, without vulnerability details or sensitive attachments. No private email address or response deadline is promised here.

A private report should include the affected commit/version, architecture, Home Assistant/Supervisor versions, prerequisites, impact, sanitized reproduction steps, and whether the issue requires relaxing the default boundary. Coordinate disclosure with the maintainer and affected upstream projects. Until a fix is available, stop the add-on if the risk applies to your deployment.
