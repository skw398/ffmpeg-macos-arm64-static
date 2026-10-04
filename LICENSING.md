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

This is an unofficial build. It does not represent endorsement by FFmpeg or
any dependency's authors. Software is provided without warranty, subject to
the applicable license terms. Inclusion of a codec or patent notice is not a
claim that every use is covered by a patent license.
