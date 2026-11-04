# On-demand amd64 CI runner

The native amd64 container job can run in a dedicated QEMU/KVM guest on Mu.
It is **not a runner installed on the Mu host**. The guest is provisioned with
**12 vCPUs and 8 GiB RAM**, its own disk and Docker daemon. Native aarch64 jobs
continue to use GitHub's `ubuntu-24.04-arm` runners; metadata and Python jobs
remain on `ubuntu-24.04`.

## Trust and routing

`.github/workflows/ci.yml` selects the label array
`[self-hosted, linux, x64, mu-voxl-amd64]` only when all of these are true:

- `github.repository == 'ColeMundus/ha-voxl-chromium'`
- `github.ref == 'refs/heads/main'`
- The event is `push`, `workflow_dispatch`, or `schedule`.

Every other amd64 job uses `ubuntu-24.04`. This includes public PRs, forks,
Dependabot/upstream-update branches, manual runs on non-main refs, and release
tags. Reusable CI inherits its caller's event/ref: a release tag's `push` is
still hosted because its ref is not `refs/heads/main`. Do not change checkout to
run a PR head or user-supplied ref from an otherwise trusted main job.

These are checked-in workflow rules, **not a runner-level authorization ACL**.
Review changes to workflows and runner labels as security-sensitive. An approved
PR that rewrites its workflow to request self-hosted labels could bypass the
routing code; do not approve such runs. The controller's trusted-run filter
controls when the VM boots, not which queued jobs GitHub can assign while it is
online. Keep repository Actions approvals/restrictions in place; never treat a
public-repository self-hosted runner as safe for arbitrary contributor code.

## Power lifecycle

The host-side controller in [`controller.py`](controller.py) polls GitHub every
**20 seconds**, starts the guest only for a queued trusted job requesting
`mu-voxl-amd64`, and stops it after **60 seconds of confirmed idle time**.
Boot time is additional; a queued job need not start immediately. Busy runners,
in-progress jobs, startup grace and uncertain API/runner state prevent an idle
shutdown. API errors must not be interpreted as permission to stop an active VM.

The provisioned host units are:

- `gh-voxl-controller.service`: the always-available polling controller.
- `gh-voxl-vm.service`: QEMU/KVM, started and stopped on demand.

**Offline between jobs is normal.** A registered runner remains visible as
`offline` in GitHub while its VM is off; it is not meant to consume 8 GiB of RAM
at idle. Do not enable an unconditional VM boot or add a second competing
controller. Controller credentials stay in the host's GitHub CLI configuration;
do not copy the host home directory or its credentials into the guest.

Read-only host diagnostics:

```sh
systemctl status gh-voxl-controller.service gh-voxl-vm.service
journalctl -u gh-voxl-controller.service -u gh-voxl-vm.service -n 100 --no-pager
gh api repos/ColeMundus/ha-voxl-chromium/actions/runners
```

Check that the queued job is trusted and requests the exact label before
investigating a boot delay. Service installation, credentials, guest registration
and lifecycle configuration are host provisioning, not workflow steps.

## Guest permissions and isolation

- The guest connects outbound to GitHub and dependency/image services. There are
  **no Internet-facing forwarded ports**. Maintenance SSH is forwarded only on
  host loopback, `127.0.0.1:2227`, not `0.0.0.0`.
- No host home-directory mounts and no host Docker socket are passed through.
  Docker commands and the scanner's Docker-socket mount operate on **guest
  Docker only**. Guest root is not intended to confer host root.
- The runner account has passwordless sudo **inside the VM**. The workflow runs
  `uv run playwright install --with-deps chromium` there, including guest package
  dependencies; no Mu host packages or sudo policy need changing for this step.
- `astral-sh/setup-uv` installs in the runner's effective guest `$HOME`; keep that
  home writable. Do not redirect installation into the checkout or host home.
- NAT/outbound access is not an air gap or a guarantee that LAN services are
  unreachable. Only reviewed main-branch code belongs here.

Mu additionally applies [`vm-firewall.nft`](vm-firewall.nft) to the dedicated
QEMU host UID. It rejects new private-LAN, Tailscale, loopback and link-local
destinations, while allowing the host DNS stub and established maintenance SSH
replies. Public GitHub access and rejection of the private Home Assistant address
were exercised. These rules affect only that QEMU UID, not other host services.
All external fork contributors require Actions approval on the repository; review
workflow changes before approving, since labels are not authorization boundaries.

## Persistent workspace and smoke-test safety

The VM disk is persistent, not a fresh machine for each job. CI removes the
contents of **this checkout's `GITHUB_WORKSPACE`** before checkout and again in
an `always()` final step. Checkout uses `persist-credentials: false`. Temporary
files use a per-run/attempt/architecture directory under `RUNNER_TEMP`; cleanup
removes only that directory, not all runner temporary files. A forced VM kill
can bypass final cleanup, so the next job also cleans before checkout.

The smoke suite owns its containers, volume and `172.30.32.0/24` network and
normally removes them in its teardown. A stable, job-level concurrency group
serializes Mu amd64 builds/smoke tests without cancelling an in-progress build;
hosted jobs use unique groups and independent Docker daemons. GitHub concurrency
can replace an older **pending** job with a newer pending job; it is not a FIFO
queue retaining every build. Do not run another smoke test manually against this
guest daemon during CI. After an interrupted run, inspect and remove only the
specific abandoned resources if necessary; never add broad Docker/system/volume
pruning to a workflow.

Docker build layers, downloaded images and the scanner database volume can
persist for reuse. Monitor guest disk use separately. Rust build parallelism is
a Docker-build concern: a workflow-level `CARGO_BUILD_JOBS` alone does not enter
a Docker `RUN`. If 12-way compilation exceeds the guest's 8 GiB limit, explicitly
bound Cargo inside the Docker build and verify the resulting build; do not
silently enlarge or modify the Mu host.

## Verification scope

`uv run pytest tests/test_runner_routing.py` parses the real CI YAML, checks the
exact approved trust predicate and JSON label arrays against an event/ref/repo
routing table, checks release-call behavior and concurrency, and executes the
cleanup/preparation shell against isolated temporary fixtures. `actionlint`
validates GitHub expression/context syntax. These local checks do **not** prove
that a guest successfully registered, woke, completed CI or shut down; use actual
controller logs and GitHub job results for those operational claims. See
[`TESTING.md`](../TESTING.md) for the initial remote CI results and limitations.

The full run linked there subsequently verified actual automatic wake, successful
amd64 CI execution and cleanup, and automatic idle poweroff. The final VM state
was `inactive` with `MainPID=0`; GitHub showed the runner offline and not busy.
