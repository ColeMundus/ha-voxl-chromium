# Licensing and attribution

## Project and third-party licenses

Original project code and documentation are **GPL-2.0-or-later**: GNU GPL version
2, or at your option any later version. [LICENSE](LICENSE) contains the unmodified
GPL version 2 text. Independent bundled programs retain their own licenses.

| Component | License |
| --- | --- |
| Add-on scripts, configuration and documentation | GPL-2.0-or-later |
| [voxl source](https://github.com/tembo/voxl) | MIT, copyright © 2026 Tembo |
| voxl executable linked with x264 | GPL obligations apply to the linked work |
| x264 | GPL-2.0-or-later; verify the exact packaged notices |
| Chromium, Debian runtime programs and other dependencies | Their respective licenses and third-party notices |

The listing images `voxl_chromium/icon.png` and `voxl_chromium/logo.png` are
unmodified [Chromium product icons](https://chromium.googlesource.com/chromium/src/+/69b49b65cdf99ce36bcac08e7c4918a7f8a13458/chrome/app/theme/chromium/).
Their copyright and BSD license are reproduced in
[CHROMIUM-LICENSE](voxl_chromium/CHROMIUM-LICENSE). The companion integration's
`custom_components/voxl_chromium/brand/` directory carries copies of the same
artwork and its BSD notice for Home Assistant's local branding API. No upstream
endorsement is implied.

The checksum-verified original voxl archive is modified by the exact dependency
patch in this repository. Preserve the patch, original license and provenance
when distributing sources; see [MODIFICATIONS.md](voxl_chromium/MODIFICATIONS.md).

## Source-build distribution

This project distributes **source only**, not prebuilt container images or
executables. Supervisor builds the image locally. Tagged source releases include
this project's tracked source, the exact pinned voxl archive and original lockfile,
local patches, provenance and checksums. The original lockfile plus the patch
specifies the effective dependency lock used by the build.

That bundle is **not complete corresponding source for every executable in a
locally built image**. Chromium, Debian packages, Rust dependencies and build tools
have their own obligations. Building a local image is distinct from distributing
it; exporting, mirroring or pushing that image can create redistribution duties.

**Do not publish binaries until a separately reviewed corresponding-source process
is implemented.** Inventory the exact distributed versions and modifications,
preserve component notices, and provide the matching machine-readable sources
and required build/install scripts with appropriate access and retention. A
Dockerfile, LICENSE file or upstream link alone does not establish compliance.
No written source offer is created by this notice. Codec patents are a separate
question from copyright licensing. This is maintenance policy, not legal advice
or a certification of compliance.

The source-release checklist is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Upstream voxl MIT notice

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

- [GNU GPL version 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)
- [Pinned voxl license](https://github.com/tembo/voxl/blob/c9b42e6567095cd0f9a23bf2ba8684daaa0464a5/LICENSE)
- [Upstream binary-licensing explanation](https://github.com/tembo/voxl#whats-the-license)
- [Debian source packages](https://www.debian.org/doc/debian-policy/ch-source.html)

Check `voxl_chromium/upstream.json` for the current build pin. These reference
links are not substitutes for release-matched sources when distributing binaries.
