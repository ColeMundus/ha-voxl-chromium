# Initial development verification

These results describe the locally exercised 0.1.0 source-build development
version, not a certification of future updates or a substitute for CI results.

## Executed locally on Linux amd64

| Check | Result |
| --- | --- |
| Python suite on Python 3.11 | 51 passed |
| Ruff lint and format | Passed |
| Pinned Home Assistant add-on metadata linter | Passed |
| GitHub Actions actionlint | Passed |
| Docker source build with patched dependency lock | Passed |
| Upstream voxl library tests during the image build | 15 passed; 1 encoding benchmark ignored |
| Trivy Debian image scan, fixable HIGH/CRITICAL | No findings |
| Trivy effective Rust lockfile scan, fixable HIGH/CRITICAL | No findings |

The real-container smoke suite exercised:

- Supervisor-source-IP emulation and Ingress prefix stripping, including relative
  script and worker URLs and WebSocket upgrades.
- Rejection of an untrusted peer despite forged forwarding headers; no published
  application ports.
- Actual H.264 WebCodecs decoding in a Chromium viewer.
- Mouse and keyboard input reaching a real page in the remote Chromium browser.
- PulseAudio/Opus packets over the WebSocket (not a subjective audio-quality test).
- File upload/download, directory ZIP downloads validated by Python's ZIP reader,
  and file-root traversal rejection.
- Browser UID 1000 and absence of inherited Supervisor tokens.
- Browser localStorage and downloads surviving a container restart.
- Dynamic resolution following the viewer's dimensions.
- Core-service failure exiting the container instead of leaving a dead UI.

## Security dependency correction

The original voxl 0.3.2 lockfile's `zip` 7.3.0 failed the Rust dependency scan.
The documented source patch upgrades it to exactly 9.0.5. The final lockfile,
image, upstream tests, ZIP-download test, and full smoke suite were rechecked.
See [MODIFICATIONS.md](voxl_chromium/MODIFICATIONS.md) for scope and provenance.

The vulnerability commands intentionally use `--severity HIGH,CRITICAL` and
`--ignore-unfixed`. Passing means no findings under those filters and the database
used at execution time, **not** absence of all vulnerabilities. CI runs both
package and lockfile scans on subsequent builds.

## Not yet verified

- Native aarch64 runtime execution (configured in GitHub CI).
- Actual Home Assistant OS/Supervisor installation and HTTPS Ingress integration.
- Live Supervisor backup/restore or an installed-version upgrade.
- Remote GitHub Actions execution and release publication.

The fixture proxy emulates Ingress behavior; it is not Home Assistant itself.
The repository is intentionally marked experimental. See
[CONTRIBUTING.md](CONTRIBUTING.md) for commands and requirements.
