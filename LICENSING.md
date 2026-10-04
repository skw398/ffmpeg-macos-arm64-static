# License scope

The MIT license in `LICENSE` covers this project's original build scripts,
Python tools and tests, workflow definitions, recipe data and documentation.
It does not relicense FFmpeg, its dependencies, upstream-derived patches,
reproduced license notices or patent grants.

The configured FFmpeg binaries (`ffmpeg`, `ffprobe` and `ffplay`) are distributed
under GPL-3.0-or-later. The configuration uses `--enable-gpl --enable-version3`
and does not use `--enable-nonfree`. Individual components retain their own
copyright notices, licenses and applicable additional terms. See `deps.txt`,
`scripts/data/licenses.json`, the release SBOM and `share/licenses/` in the
binary package. Patches containing upstream code retain that code's license;
the top-level MIT license applies only to this project's original material.

Each release includes a matching `-sources.tar.xz` archive containing the
fixed upstream sources, Rust dependencies, build scripts, local patches and
build instructions. See `BUILDING.md`. The SPDX document's CC0-1.0 data license
applies to its metadata; reproduced license texts retain their original terms.

The upstream sources are modified as described in `SOURCE-CHANGES.md`, which
records the affected files, changes, maintainer and relevant dates. Both the
binary package and matching source package include that notice.

## Warranty and liability

The original project material is subject to the MIT license's disclaimer of
warranty and limitation of liability in `LICENSE`. The FFmpeg binaries are
subject to the GPL's disclaimer of warranty and limitation of liability;
see GPLv3 sections 15–17 in the included upstream license texts. Section 16
also covers parties that modify or convey the program as permitted by the GPL.
These provisions apply to the extent permitted by applicable law. This
document explains their scope and does not replace the upstream licenses
or impose additional conditions on the rights they grant.

## Source availability and support

For every binary release kept available for download, the matching
`-sources.tar.xz`, build instructions and checksums are kept available alongside
it. A newer release does not replace an older release's corresponding source.
This distribution uses GPLv3 section 6(d) to provide source access with the
binary download.

Only the latest published release is supported for updates. Keeping historical
source packages available does not promise updates to those releases. No
support service, response deadline or update schedule is guaranteed.

This is an unofficial build. It does not represent endorsement by FFmpeg or
any dependency's authors. Software is provided without warranty, subject to
the applicable license terms. Inclusion of a codec or patent notice is not a
claim that every use is covered by a patent license.
