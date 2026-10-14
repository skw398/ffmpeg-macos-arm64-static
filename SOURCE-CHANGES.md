# Modifications to upstream sources

This distribution is built by skw398 from the sources pinned in `deps.txt`,
with the modifications described below. It is not an unmodified upstream
build. Notice updated: 2026-10-13. Dates below record when the modifications
were introduced or revised in this project's history, not the date on which
an individual CI build applies them.

The source archive keeps the original upstream archives unchanged. The
included patches and build scripts reproduce these modifications. Upstream
copyright and license notices remain applicable to the affected code.

## Explicit patches

The six dependency patches below were introduced by skw398 on 2026-09-27.
The FFmpeg patch was introduced on 2026-10-12. The libssh patch was
retargeted to libssh 0.12.2 on 2026-10-13. Paths in the second column are
relative to the corresponding upstream source directory.
`scripts/data/patches.txt` selects the patches applied by `scripts/build-deps.sh`
and `scripts/build-ffmpeg.sh`, using the shared helper in `scripts/lib.sh`.
The XEVE NEON helper rename was removed on 2026-10-12 because OpenAPV 1.1.3.0
no longer exports the conflicting helper.

| Component and pinned version | Changed files | Modification and patch |
|---|---|---|
| FFmpeg `9.0.2` | `libavcodec/liboapvenc.c` | Pass a zero-initialized metadata creation descriptor to OpenAPV's API set 1. Adapted from upstream commit `c54710db21c1827dbc3e47658a562525af0fe528` for the pinned OpenAPV version. Patch: `patches/ffmpeg/9.0.2/001-openapv-metadata-api.patch`. |
| libssh `libssh-0.12.2` | `CMakeLists.txt` | Replace incompatible compiler checks with the standard `CheckCCompilerFlag` module. Patch: `patches/libssh/libssh-0.12.2/001-cmake4-compilerchecks.patch`. |
| libqrencode `v4.1.1` | `configure.ac` | Restore the `VERSION` definition used by the API version function. Patch: `patches/libqrencode/v4.1.1/001-autoconf-version-define.patch`. |
| libcaca `0.99.beta20` | `src/common-image.c` | Include the internal header declaring `_caca_alloc2d`. Patch: `patches/libcaca/0.99.beta20/001-internal-header-include.patch`. |
| libflite `v2.2` | `main/Makefile` | Replace a GNU-specific `cp -pd` install command with a macOS-compatible command. Patch: `patches/libflite/v2.2/001-cp-pd-macos.patch`. |
| libcodec2 `1.2.0` | `src/c2sim.c`, `src/codec2.c`, `src/interp.c`, `src/lsp.c`, `src/lsp.h`, `src/quantise.c` | Rename the `lsp_to_lpc` / `lpc_to_lsp` helpers to avoid static-link collisions with Speex. Patch: `patches/libcodec2/1.2.0/001-rename-speex-helpers.patch`. |
| libzmq `v4.3.5` | `external/sha1/sha1.c`, `external/sha1/sha1.h` | Rename SHA-1 helpers to avoid static-link collisions with libssh. Patch: `patches/libzmq/v4.3.5/001-rename-sha1.patch`. |

## Conditional build-file changes

These transformations are maintained by skw398 and only change files when
their matching content is present. The exact rules are in the supplied scripts;
they apply to the pinned components, including their supplied subdirectories.

| Introduced / revised | Target | Transformation and implementation |
|---|---|---|
| 2026-09-27 / 2026-10-04 | `CMakeLists.txt` and `*.cmake` within the first five levels of fetched source directories | Remove bare `-Werror` options while retaining warning-specific options. Exclude `.cargo-vendor` and preserve its file checksums. Implemented by `fetch()` in `scripts/lib.sh`. |
| 2026-09-27 | `configure` in source directories using the `autotools` recipe | Remove the obsolete Darwin linker option `-force_cpusubtype_ALL`. Implemented by `build_generic()` in `scripts/build-deps.sh`. |
| 2026-09-27 | Generated `Makefile` files under source directories using the `autotools` recipe | Remove `-Werror` and `-Werror=<warning>` options after configure. Implemented by `build_generic()` in `scripts/build-deps.sh`. |
| 2026-09-27 | Autotools components without an executable `configure` | Run the upstream bootstrap script or `autoreconf -fi` to generate configuration files. Refresh timestamps on pre-generated Autotools files to prevent unwanted regeneration. Implemented by `build_generic()` in `scripts/build-deps.sh`. |
| 2026-10-04 | librav1e `.cargo/config.toml` and the added `.cargo-vendor/` directory when rebuilding with `SOURCE_PACKAGE` | Append source replacement configuration and copy the supplied, checksum-verified Cargo dependencies. Preserve the pinned `Cargo.lock` and existing build settings. Implemented by `extract()` in `scripts/release_sources.py`. |

Compiler flags and other selected build options are recorded in
`scripts/data/build-deps.txt`, the build scripts and the packaged
`build-info/buildconf.txt`. Generated build outputs and installation metadata
are recreated by those scripts. `BUILDING.md` describes how to rebuild or
apply further modifications; `LICENSING.md` describes the license scope.
