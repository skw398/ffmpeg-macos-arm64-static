# Rebuilding a release from its source package

Use the `ffmpeg-macos-arm64-static-<version>-sources.tar.xz` asset published next
to the matching binary archive, SBOM and SHA256SUMS. Verify SHA256SUMS before
unpacking it. The archive has a `ffmpeg-macos-arm64-sources/` top-level directory:

- `project/`: the build scripts, recipes, patches, composite Action and license scope
  used for the release. `SOURCE-CHANGES.md` identifies local modifications and
  their dates. `manifest.json` records the project commit.
- `upstream/`: unchanged, fixed source tarballs and Git commit snapshots for
  every pin in `project/deps.txt`. Manifest entries record the original pin and
  the SHA256 of each packaged file. `project/patches/` and `project/scripts/lib.sh`
  describe the explicit patches and generic source edits applied by the build.
- `cargo/`: the Cargo.lock, dependency sources and source configuration for
  Rust components, including their license documents.
- `build-info/`: the actual toolchain record and FFmpeg configuration.

Build on a macOS Apple Silicon machine with the Xcode/SDK and tool versions
recorded in `build-info/` and `project/.github/actions/build/action.yml`.
General-purpose build tools and Apple SDKs are not part of this source package.
This project's development and functional verification run only on GitHub
Actions Apple Silicon runners; do not build or run the binaries on the
maintainer's local Mac.

Install the tools and set `MACOSX_DEPLOYMENT_TARGET=11.0`, following the composite
Action's toolchain steps. Export `SOURCE_PACKAGE` as the absolute path to the
unpacked `ffmpeg-macos-arm64-sources` directory, and work in its `project/`:

```sh
export SOURCE_PACKAGE="$(cd /path/to/ffmpeg-macos-arm64-sources && pwd)"
cd "$SOURCE_PACKAGE/project"
sh scripts/build-deps.sh
sh scripts/build-ffmpeg.sh --no-chromaprint
sh scripts/build-chromaprint.sh
sh scripts/build-ffmpeg.sh
```

`SOURCE_PACKAGE` makes source preparation use the supplied archives, verify
manifest hashes and source pins, and apply the included build recipe. Git
cloning and source downloads are skipped. Rust source replacement uses the
supplied vendor directory. Tool installation may still require the network.

To change source behavior, add your patch to `project/patches/` and list it in
`project/scripts/data/patches.txt`, or change the included build scripts. Source
preparation reconstructs a clean upstream tree each time; edits made directly
in generated `build/src/` trees are overwritten by subsequent preparation.
Use a fresh build directory after changing the recipe. Read `LICENSING.md` and
the upstream license notices before redistributing a modified build.

The source package enables rebuilding and modifying the supplied sources. It
does not promise byte-for-byte reproducibility across runner image updates or
Apple SDK changes. Release CI checks the source inventory, file hashes and build metadata.
A complete rebuild from the source package is a separate functional check.
