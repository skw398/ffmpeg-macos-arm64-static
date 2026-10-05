"""Exercise the shell installer with local fixture downloads, never real binaries."""

import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'install-release.sh'
BINARIES = ('ffmpeg', 'ffprobe', 'ffplay')


class InstallReleaseTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = 'ffmpeg-macos-arm64-static-vfixture-r1'
        self.archive = self.root / f'{self.package}.tar.xz'
        self.bin_dir = self.root / 'bin'
        self.bin_dir.mkdir()
        self.content = struct.pack('<II', 0xfeedfacf, 0x0100000c) + b'fixture, not executable'
        fake_commands = self.root / 'commands'
        fake_commands.mkdir()
        (fake_commands / 'uname').write_text('''#!/bin/sh
case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; *) exit 1 ;; esac
''')
        (fake_commands / 'curl').write_text('''#!/bin/sh
set -eu
output=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    -w) printf 'https://github.com/skw398/ffmpeg-macos-arm64-static/releases/tag/vfixture-r1'; exit 0 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
cp "$FIXTURE_RELEASE_DIR/${url##*/}" "$output"
''')
        for command in fake_commands.iterdir():
            command.chmod(0o755)
        self.env = os.environ | {'PATH': str(fake_commands) + os.pathsep + os.environ['PATH'],
                                 'FIXTURE_RELEASE_DIR': str(self.root)}

    def make_archive(self, *, missing=None, bad=None, symlink=None):
        with tarfile.open(self.archive, 'w:xz') as archive:
            for name in BINARIES:
                if name == missing:
                    continue
                info = tarfile.TarInfo(f'{self.package}/bin/{name}')
                if name == symlink:
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/outside'
                    archive.addfile(info)
                else:
                    content = b'not Mach-O' if name == bad else self.content
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            info = tarfile.TarInfo('../outside')
            info.size = 7
            archive.addfile(info, io.BytesIO(b'ignored'))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.root / 'SHA256SUMS').write_text(f'{digest}  ./{self.archive.name}\n')

    def run_installer(self):
        return subprocess.run(['sh', str(SCRIPT), str(self.bin_dir)], env=self.env,
                              text=True, capture_output=True)

    def test_installs_three_executable_files_without_following_existing_symlink(self):
        self.make_archive()
        original = self.root / 'original'
        original.write_bytes(b'keep')
        (self.bin_dir / 'ffmpeg').symlink_to(original)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vfixture-r1', result.stdout)
        self.assertEqual(original.read_bytes(), b'keep')
        self.assertFalse((self.bin_dir / 'ffmpeg').is_symlink())
        for name in BINARIES:
            self.assertEqual((self.bin_dir / name).read_bytes(), self.content)
            self.assertEqual((self.bin_dir / name).stat().st_mode & 0o777, 0o755)
        self.assertEqual({path.name for path in self.bin_dir.iterdir()}, set(BINARIES))
        self.assertFalse((self.root / 'outside').exists())

    def test_invalid_archive_leaves_existing_files_untouched(self):
        for options in ({'missing': 'ffplay'}, {'bad': 'ffplay'}, {'symlink': 'ffplay'}):
            with self.subTest(options=options):
                self.make_archive(**options)
                for name in BINARIES:
                    (self.bin_dir / name).write_bytes(b'old')
                self.assertNotEqual(self.run_installer().returncode, 0)
                self.assertTrue(all((self.bin_dir / name).read_bytes() == b'old' for name in BINARIES))

    def test_directory_target_stops_before_any_replacement(self):
        self.make_archive()
        (self.bin_dir / 'ffmpeg').write_bytes(b'old')
        (self.bin_dir / 'ffplay').mkdir()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('target is a directory', result.stderr)
        self.assertEqual((self.bin_dir / 'ffmpeg').read_bytes(), b'old')
        self.assertFalse((self.bin_dir / 'ffprobe').exists())

    def test_bad_missing_or_duplicate_checksum_prevents_installation(self):
        self.make_archive()
        checksums = self.root / 'SHA256SUMS'
        valid = checksums.read_text()
        for content in (valid * 2, '0' * 64 + valid[64:], valid.replace(self.archive.name, 'other.tar.xz')):
            with self.subTest(content=content):
                checksums.write_text(content)
                self.assertNotEqual(self.run_installer().returncode, 0)
                self.assertEqual(list(self.bin_dir.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
