# Initial development verification

These results describe the locally exercised 0.1.0 source-build development
version, not a certification of future updates or a substitute for CI results.

## Executed locally on Linux amd64

This table records the initial development baseline, before the scanner and
runner regression tests were added; it is not the current test-suite total.

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

## Initial GitHub CI execution

The public repository's [first CI run](https://github.com/ColeMundus/ha-voxl-chromium/actions/runs/41753324664)
was **not fully green**. Its step results establish:

| Check | Remote result |
| --- | --- |
| Metadata lint, Python tests and Ruff | Passed |
| Native amd64 source image build and real-container smoke | Passed |
| Native aarch64 source image build and real-container smoke | Passed |
| Vulnerability scanning | Failed in Trivy's shell download installer (exit 6), before scan results |
| Effective Rust dependency scan | Skipped after the installer failure |

The scanner action's download installer has been replaced with a version- and
digest-pinned Trivy container built from `ci/Dockerfile`. The replacement and its
regression test were exercised locally; **a complete remote rerun with the
replacement scanner has not yet been verified**. The initial run is evidence of
native ARM execution, not evidence that either remote security gate passed.

## On-demand runner verification

The routing regressions parse the real YAML, check the exact trusted-main
predicate and JSON runner labels across repository/ref/event combinations, and
cover reusable release-tag calls, always-hosted ARM, non-cancelling job-level
concurrency, and scoped workspace/temp cleanup. Cleanup/preparation shell commands
are executed against isolated temporary fixtures, without real sudo. Workflow
syntax/context availability is checked with actionlint.

These local checks are not an end-to-end VM lifecycle result. The on-demand
amd64 runner (12 vCPUs, 8 GiB RAM) uses a 20-second controller poll and a 60-second
confirmed-idle shutdown; offline between jobs is expected. Only canonical trusted
main jobs may use it, and ARM remains GitHub-hosted. See
[runner/README.md](runner/README.md) for the operational and isolation boundaries.

## Not yet verified

- Full remote CI success after the scanner replacement and on-demand routing changes.
- End-to-end guest wake, job completion and idle shutdown with the new routing.
- Actual Home Assistant OS/Supervisor installation and HTTPS Ingress integration.
- Live Supervisor backup/restore or an installed-version upgrade.
- Release publication.

The fixture proxy emulates Ingress behavior; it is not Home Assistant itself.
The repository is intentionally marked experimental. See
[CONTRIBUTING.md](CONTRIBUTING.md) for commands and requirements.
