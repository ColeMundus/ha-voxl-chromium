# Local upstream modifications

The source archive identified by `upstream.json` remains unmodified and
checksum-verified. Before compilation this add-on applies
`patches/zip-security.patch` to upstream `Cargo.toml` and `Cargo.lock`.

The patch upgrades the `zip` dependency from 7.3.0 to exactly 9.0.5 and updates
its lockfile/checksum. It addresses the dependency versions flagged by
CVE-2026-115905, CVE-2026-115907, CVE-2026-115908, and CVE-2026-115909.
No upstream Rust or web application source is otherwise changed. The reported
issues are dependency advisories; this is not a claim that every advisory is
reachable through voxl's use of ZIP creation.

The build fails if the patch cannot apply, runs upstream library tests, and
builds with `--locked`. The effective patched `Cargo.lock`, this notice, and the
patch are retained under `/usr/share/doc/voxl/` in local builds. CI scans that
actual lockfile separately from Debian packages. Review or remove the patch
when accepting a newer upstream release; do not bypass a failed patch or scan.

The original voxl MIT notice is preserved. The original archive and this
repository's exact patch together describe the voxl source used for this build.
The project distributes source only; see the root `NOTICE.md` before distributing
any locally built executable or container image.
