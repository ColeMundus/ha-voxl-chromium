# Licensing, attribution, and source delivery

## Project license and scope

Original code and documentation contributed to this project are licensed under the **GNU General Public License, version 2 or (at your option) any later version** (`GPL-2.0-or-later`). See [LICENSE](LICENSE), the unmodified GPL version 2 text fetched from [gnu.org](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt). The “or later” permission is this project's grant, not a change to the standard license text.

This is a component-based distribution, not a claim that everything in a container has one license:

| Component | Licensing treatment |
| --- | --- |
| This project's original add-on scripts, configuration, and documentation | GPL-2.0-or-later |
| [voxl source](https://github.com/tembo/voxl) | MIT, copyright © 2026 Tembo; notice reproduced below |
| voxl executable linked with x264 | GPL obligations apply to the linked work; upstream explicitly documents this distinction from its MIT source license |
| [x264](https://code.videolan.org/videolan/x264) | GPL version 2 or later in upstream source headers; verify the exact packaged version and notices used in a build |
| Debian runtime programs, including Chromium, nginx, Xvfb, and PulseAudio | Independent programs retain their own component licenses and notices; Chromium also contains many third-party components |
| Other libraries, Rust crates, and frontend dependencies | Their own licenses and attribution requirements; audit the exact locked versions and what is included in the resulting work |

Bundling independent programs does not, by itself, relicense them under this project's GPL grant. Nor does that distinction remove their individual redistribution obligations. Static versus dynamic linking is not a safe shortcut for ignoring x264's GPL terms. This notice is a practical maintenance policy, **not legal advice or a certification of compliance**. Codec patent questions are separate from copyright licensing and may depend on jurisdiction and use.

## Initial distribution: source only

Local build changes are recorded in
[`voxl_chromium/MODIFICATIONS.md`](voxl_chromium/MODIFICATIONS.md). In particular,
the original checksum-verified voxl archive is patched to use `zip` 9.0.5;
the repository includes the exact manifest/lockfile patch and preserves upstream
licensing. Release sources must include that patch, not just the original archive.

The initial release deliberately **does not distribute prebuilt container images**. Home Assistant Supervisor builds `voxl_chromium/Dockerfile` locally; no prebuilt-image `image` key or GHCR publishing is provided. CI builds containers only for ephemeral testing, without publishing them or uploading binary container artifacts.

Release packages are intended to include this add-on's source and **the exact voxl source revision used by the build**, with checksums and its license. These are source releases, **not a claim that the package contains complete corresponding source for every executable that a local Docker build obtains or produces**. Debian packages, Rust/frontend dependencies, the base image, and build tools must also be considered for any later binary distribution. Before publication, verify that the actual source archive contains the promised files; a GitHub-generated repository archive need not include separately downloaded upstream sources.

This policy intentionally avoids shipping prebuilt GPL binary images without an implemented complete corresponding-source delivery process. It does not declare every legal issue solved. Building and running a local copy is distinct from distributing that copy; sharing, exporting, mirroring, or pushing your locally built image can create redistribution obligations.

## Source-release checklist

Before publishing a source release, maintainers should:

1. Record the add-on commit/version, immutable voxl commit, source URL, and verified archive SHA-256. Keep the exact upstream archive or an equivalent complete source snapshot with all local patches and the MIT notice; do not substitute a moving branch URL.
2. Include this repository's build/configuration scripts, lockfiles that actually control the build, license files, and a description of any voxl modifications. Preserve upstream copyright notices and mark modified files as required by their licenses.
3. Publish the exact voxl source alongside the add-on source package and provide an inventory identifying both. Verify downloads and checksums from the release as a recipient would. Do not describe an unassembled package as already available.
4. Keep release notes clear that no prebuilt image is supplied, identify intended versus actually tested architectures, and state that this source package is not a complete binary-image compliance bundle.

## If you later distribute a built image or executable

**Stop and establish a source-delivery process before publishing binaries.** A root LICENSE, this notice, a Dockerfile that fetches dependencies, or links to upstream repositories alone do not establish compliance. Recommended preparation:

1. Inventory the **actual image**: record its digest, base-image digest, Debian binary/source package versions, exact voxl/x264 revisions, Rust/frontend lockfiles and dependencies, local patches, build flags, and build/install scripts. Preserve package copyright/license files such as `/usr/share/doc/<package>/copyright`; inspect the final image rather than assuming these survived image cleanup. This table is not a complete inventory.
2. Assemble and retain the complete corresponding machine-readable source required for each distributed GPL-covered work: the precise source and modifications for the linked voxl/x264 work and other covered dependencies, interface definitions, and compilation/installation scripts. Collect exact Debian source packages, including upstream tarballs, Debian patches, and packaging files, for covered redistributed packages. Do not assume the GPL operating-system component exception excuses everything supplied inside the image. Audit non-GPL notices and source obligations separately.
3. Prefer making the corresponding source available with equivalent access alongside the binary distribution, with an unambiguous mapping from each image digest to its source archive and notices. For registry distribution, design and review how recipients obtain the matched sources rather than assuming a remote repository link is sufficient. Check that retained archives can actually be downloaded and used to rebuild modified versions.
4. If choosing GPLv2 §3(b)'s written-offer route instead, implement a real offer accompanying each binary, valid for **at least three years**, covering **any third party** and charging no more than the cost of physically performing source distribution. Provide a durable contact/delivery channel, retain the sources, and honor requests. No such offer or service is established by this document. GPLv2 §3(c) is a narrow noncommercial pass-through alternative, not the default for a new build.
5. Review the finished notices, source access, dependency licensing, and retention process before release. Moving upstream links, disappearing package mirrors, or a passing CI build do not discharge the distributor's obligations. Get qualified legal advice where needed.

For exact Debian sources, consult package `Source` metadata and Debian source repositories, using `apt-get source` with matching `deb-src` entries or archived sources such as [snapshot.debian.org](https://snapshot.debian.org/) when necessary. Preserve the actual source files and version records; a future ability to fetch them is not itself source delivery.

## voxl MIT notice

The following notice is reproduced from upstream's `LICENSE`. Retain it with copies or substantial portions of voxl source, including when that source is incorporated into a GPL-covered executable.

```text
MIT License

Copyright (c) 2026 Tembo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## References

- [GNU GPL version 2, especially §§1–3](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)
- [voxl license at the initial pinned revision](https://github.com/tembo/voxl/blob/c9b42e6567095cd0f9a23bf2ba8684daaa0464a5/LICENSE) and [that revision's binary licensing explanation](https://github.com/tembo/voxl/blob/c9b42e6567095cd0f9a23bf2ba8684daaa0464a5/README.md#whats-the-license); check `voxl_chromium/upstream.json` for the build's current pin
- [x264 upstream header and licensing grant](https://code.videolan.org/videolan/x264/-/blob/master/x264.h)
- [Chromium license](https://chromium.googlesource.com/chromium/src/+/main/LICENSE) and the exact Debian package's copyright metadata
- [Debian Policy: source packages](https://www.debian.org/doc/debian-policy/ch-source.html)
- [Software Freedom Law Center: Guide to GPL Compliance](https://softwarefreedom.org/resources/2014/SFLC-Guide_to_GPL_Compliance_2d_ed.html)

These upstream links explain provenance and policy; they are not substitutes for release-matched source archives or a final dependency/license audit.
