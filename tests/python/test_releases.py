import io
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ci'))
sys.path.insert(0, str(ROOT / 'src/DevScripts'))
import download_cef_base
import publish_release
import release_tools
import build_base


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.source_sha = 'a' * 40

    def package(self, folder, version='2.2.8', shared=True):
        path = self.root / folder
        path.mkdir()
        (path / 'package.json').write_text(json.dumps({
            'name': 'dev.voltstro.' + folder.lower().replace('-', '.'), 'version': version}))
        (path / 'source.cs').write_text('// controlled test fixture\n')
        if folder == 'UnityWebBrowser' and shared:
            (path / 'Plugins').mkdir()
            (path / 'Plugins/VoltstroStudios.UnityWebBrowser.Shared.dll').write_bytes(b'MZfixture')
        return path

    def complete_build(self):
        for target in release_tools.TARGETS:
            folders = release_tools.CORE_PACKAGES if target == 'core' else [release_tools.NATIVE_PACKAGES[target]]
            records = [release_tools.pack_package(self.package(folder), self.output) for folder in folders]
            build = {'target': target, 'source_sha': self.source_sha, 'packages': records}
            if target != 'core':
                build.update({'cef_version': '143.test', 'cefglue_sha': 'b' * 40,
                              'validation': {'cli_help_exit_code': 0}})
            (self.output / ('build-' + target + '.json')).write_bytes(release_tools.json_bytes(build))

    def assemble(self):
        with patch.object(release_tools, 'git', return_value=self.source_sha):
            release_tools.assemble(self.output)
        return json.loads((self.output / 'release-manifest.json').read_text())

    def test_package_is_deterministic_and_repeatable(self):
        source = self.package('UnityWebBrowser')
        first = release_tools.pack_package(source, self.output)
        os.utime(source / 'source.cs', (123, 123))
        second = release_tools.pack_package(source, self.output)
        self.assertEqual(first, second)
        release_tools.validate_record(self.output, first)

    def test_executable_bit_is_preserved(self):
        source = self.package('UnityWebBrowser')
        executable = source / 'engine'
        executable.write_bytes(b'fixture')
        executable.chmod(0o755)
        record = release_tools.pack_package(source, self.output)
        with tarfile.open(self.output / record['name']) as archive:
            self.assertEqual(archive.getmember('package/engine').mode, 0o755)

    def test_macos_framework_links_are_preserved_not_duplicated(self):
        source = self.package('UnityWebBrowser')
        (source / 'Current').symlink_to('source.cs')
        record = release_tools.pack_package(source, self.output)
        with tarfile.open(self.output / record['name']) as archive:
            member = archive.getmember('package/Current')
            self.assertTrue(member.issym())
            self.assertEqual(member.linkname, 'source.cs')

    def test_package_cannot_follow_external_links(self):
        source = self.package('UnityWebBrowser')
        outside = self.root / 'outside'
        outside.write_text('must not ship')
        (source / 'escape').symlink_to(outside)
        with self.assertRaises(ValueError):
            release_tools.pack_package(source, self.output)

    def test_changed_existing_package_is_not_overwritten(self):
        source = self.package('UnityWebBrowser')
        record = release_tools.pack_package(source, self.output)
        (source / 'source.cs').write_text('different fixture')
        with self.assertRaises(ValueError):
            release_tools.pack_package(source, self.output)
        self.assertEqual(release_tools.sha256(self.output / record['name']), record['sha256'])

    def test_corrupt_package_fails_validation(self):
        record = release_tools.pack_package(self.package('UnityWebBrowser'), self.output)
        with (self.output / record['name']).open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaises(ValueError):
            release_tools.validate_record(self.output, record)

    def test_core_package_requires_compiled_shared_dll(self):
        record = release_tools.pack_package(self.package('UnityWebBrowser', shared=False), self.output)
        with self.assertRaises(ValueError):
            release_tools.validate_record(self.output, record)

    def test_complete_release_has_seven_packages_and_repeatable_checksums(self):
        self.complete_build()
        manifest = self.assemble()
        self.assertEqual(len(manifest['packages']), 7)
        sums = (self.output / 'SHA256SUMS').read_bytes()
        self.assemble()
        self.assertEqual((self.output / 'SHA256SUMS').read_bytes(), sums)
        self.assertEqual(len(sums.splitlines()), 8)

    def test_incomplete_platform_matrix_fails_closed(self):
        self.complete_build()
        (self.output / 'build-osx-arm64.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.assemble()

    def test_mixed_source_commits_cannot_be_released(self):
        self.complete_build()
        path = self.output / 'build-osx-arm64.json'
        value = json.loads(path.read_text())
        value['source_sha'] = 'c' * 40
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.assemble()

    def test_mixed_cef_versions_cannot_be_released(self):
        self.complete_build()
        path = self.output / 'build-osx-arm64.json'
        value = json.loads(path.read_text())
        value['cef_version'] = 'different'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.assemble()

    def test_failed_cli_smoke_cannot_be_released(self):
        self.complete_build()
        path = self.output / 'build-win-x64.json'
        value = json.loads(path.read_text())
        value['validation']['cli_help_exit_code'] = 1
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.assemble()

    def test_binary_architectures_are_read_from_actual_headers(self):
        fixtures = {}
        for machine, target in [(0x8664, 'win-x64'), (0xaa64, 'win-arm64')]:
            data = bytearray(80)
            data[:2] = b'MZ'
            struct.pack_into('<I', data, 0x3c, 64)
            data[64:68] = b'PE\x00\x00'
            struct.pack_into('<H', data, 68, machine)
            fixtures[target] = data
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', elf, 18, 62)
        fixtures['linux-x64'] = elf
        for machine, target in [(0x01000007, 'osx-x64'), (0x0100000c, 'osx-arm64')]:
            data = bytearray(64)
            data[:4] = b'\xcf\xfa\xed\xfe'
            struct.pack_into('<I', data, 4, machine)
            fixtures[target] = data
        for target, data in fixtures.items():
            with self.subTest(target=target):
                path = self.root / target
                path.write_bytes(data)
                self.assertEqual(release_tools.architecture(path), target)

    def test_remote_asset_requires_server_side_sha256_and_complete_state(self):
        expected = {'fixture.tgz': {'sha256': 'a' * 64, 'bytes': 20}}
        asset = {'name': 'fixture.tgz', 'digest': 'sha256:' + 'a' * 64, 'size': 20, 'state': 'uploaded'}
        publish_release.verify_remote_assets({'assets': [asset]}, expected)
        for changes in [{'digest': None}, {'size': 21}, {'state': 'starter'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                publish_release.verify_remote_assets({'assets': [asset | changes]}, expected)

    def test_complete_published_release_is_a_read_only_no_op(self):
        self.complete_build()
        manifest = self.assemble()
        expected = publish_release.release_assets(manifest, self.output)
        assets = [{'id': index, 'name': name, 'digest': 'sha256:' + record['sha256'],
                   'size': record['bytes'], 'state': 'uploaded'}
                  for index, (name, record) in enumerate(expected.items())]
        release = {'draft': False, 'assets': assets, 'html_url': 'https://github.com/example/project/releases/tag/v2.2.8'}

        def fake_api(method, path, **kwargs):
            self.assertEqual(method, 'GET', 'Published releases must never be mutated on retry.')
            return (self.output / 'release-manifest.json').read_bytes() if kwargs.get('raw') else release

        with patch.object(publish_release, 'api', side_effect=fake_api), \
             patch.object(publish_release.subprocess, 'run', return_value=SimpleNamespace(stdout=self.source_sha)), \
             patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/project'}):
            publish_release.publish(self.output, 'v2.2.8-smal.1')

    def test_wrong_version_tag_is_rejected_before_github_calls(self):
        self.complete_build()
        self.assemble()
        with patch.object(publish_release, 'api') as api:
            with self.assertRaises(ValueError):
                publish_release.publish(self.output, 'v99.0.0')
            api.assert_not_called()

    def test_inconsistent_checksums_are_rejected_before_publication(self):
        self.complete_build()
        self.assemble()
        (self.output / 'SHA256SUMS').write_text('incorrect')
        with patch.object(publish_release, 'api') as api:
            with self.assertRaises(ValueError):
                publish_release.publish(self.output, 'v2.2.8-smal.1')
            api.assert_not_called()

    def publication_server(self, upload_fails=False):
        self.complete_build()
        manifest = self.assemble()
        expected = publish_release.release_assets(manifest, self.output)
        release = {'id': 42, 'draft': True, 'assets': [], 'html_url': 'https://github.com/example/project/releases/tag/v2.2.8'}
        writes = []

        def fake_api(method, path, payload=None, **kwargs):
            if method == 'GET' and '/tags/' in path:
                return None
            if method == 'POST':
                self.assertTrue(payload['draft'])
                writes.append('draft')
            if method == 'PATCH':
                self.assertEqual(len(release['assets']), 9)
                self.assertFalse(payload['draft'])
                writes.append('publish')
                release['draft'] = False
            return release

        def fake_run(args, **kwargs):
            if args[0] == 'git':
                return SimpleNamespace(stdout=self.source_sha)
            self.assertEqual(args[:3], ['gh', 'release', 'upload'])
            if upload_fails:
                raise subprocess.CalledProcessError(1, args)
            name = Path(args[4]).name
            record = expected[name]
            release['assets'].append({'name': name, 'size': record['bytes'],
                                      'digest': 'sha256:' + record['sha256'], 'state': 'uploaded'})
            return SimpleNamespace(returncode=0)

        return fake_api, fake_run, writes, release

    def test_release_is_published_only_after_all_nine_assets_verify(self):
        api, run, writes, release = self.publication_server()
        with patch.object(publish_release, 'api', side_effect=api), \
             patch.object(publish_release.subprocess, 'run', side_effect=run), \
             patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/project'}):
            publish_release.publish(self.output, 'v2.2.8-smal.1')
        self.assertEqual(writes, ['draft', 'publish'])
        self.assertFalse(release['draft'])

    def test_upload_failure_never_publishes_an_incomplete_release(self):
        api, run, writes, release = self.publication_server(upload_fails=True)
        with patch.object(publish_release, 'api', side_effect=api), \
             patch.object(publish_release.subprocess, 'run', side_effect=run), \
             patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/project'}):
            with self.assertRaises(subprocess.CalledProcessError):
                publish_release.publish(self.output, 'v2.2.8-smal.1')
        self.assertEqual(writes, ['draft'])
        self.assertTrue(release['draft'])

    def test_native_build_failure_is_not_swallowed(self):
        with patch.object(build_base.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'dotnet')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                build_base.build_cef_engine('win-x64')
            self.assertTrue(run.call_args.kwargs['check'])
            self.assertIn('-c=Release', run.call_args.args[0])

    def cef_archive(self, names):
        path = self.root / 'cef.tar.bz2'
        with tarfile.open(path, 'w:bz2') as archive:
            for name in names:
                content = b'fixture'
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        destination = self.root / 'extracted'
        destination.mkdir()
        return path, destination

    def test_cef_extract_strips_only_the_expected_root(self):
        archive, destination = self.cef_archive(['cef-test/Release/libcef.so'])
        download_cef_base.extract_cef(archive, destination, 'cef-test')
        self.assertEqual((destination / 'Release/libcef.so').read_bytes(), b'fixture')

    def test_cef_extract_rejects_path_traversal(self):
        archive, destination = self.cef_archive(['cef-test/../../escaped'])
        with self.assertRaises(tarfile.FilterError):
            download_cef_base.extract_cef(archive, destination, 'cef-test')

    def test_cef_extract_rejects_an_unexpected_archive_root(self):
        archive, destination = self.cef_archive(['different/Release/libcef.so'])
        with self.assertRaises(ValueError):
            download_cef_base.extract_cef(archive, destination, 'cef-test')

    def download_response(self, content, length=None):
        response = io.BytesIO(content)
        response.status = 200
        response.headers = {'Content-Length': str(len(content) if length is None else length)}
        return response

    def test_bad_cef_download_retries_without_weakening_the_checksum(self):
        good = b'controlled verified CEF archive fixture'
        expected = hashlib.sha1(good).hexdigest()
        archive = self.output / 'cef.tar.bz2'
        with patch.object(download_cef_base, 'urlopen', side_effect=[
                self.download_response(b'damaged'), self.download_response(good)]) as request:
            download_cef_base.download_verified('https://example.test/cef.tar.bz2', expected, archive)
        self.assertEqual(archive.read_bytes(), good)
        self.assertEqual(request.call_count, 2)
        self.assertIn('uwb_sha1=' + expected, request.call_args.args[0].full_url)
        self.assertEqual(len(list(self.output.glob('*.partial'))), 1)

    def test_short_cef_response_is_rejected_even_when_its_hash_matches(self):
        good = b'fixture'
        expected = hashlib.sha1(good).hexdigest()
        archive = self.output / 'cef.tar.bz2'
        with patch.object(download_cef_base, 'urlopen', side_effect=[
                self.download_response(good, length=100), self.download_response(good)]) as request:
            download_cef_base.download_verified('https://example.test/cef.tar.bz2', expected, archive)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(archive.read_bytes(), good)

    def test_repeated_bad_cef_downloads_fail_after_three_attempts(self):
        archive = self.output / 'cef.tar.bz2'
        with patch.object(download_cef_base, 'urlopen', side_effect=[
                self.download_response(b'bad') for _ in range(3)]) as request:
            with self.assertRaises(ValueError):
                download_cef_base.download_verified('https://example.test/cef.tar.bz2', 'a' * 40, archive)
        self.assertEqual(request.call_count, 3)
        self.assertFalse(archive.exists())
        self.assertEqual(len(list(self.output.glob('*.partial'))), 3)

    def test_missing_cef_archive_is_not_blindly_retried(self):
        error = HTTPError('https://example.test/missing', 404, 'Not Found', {}, None)
        with patch.object(download_cef_base, 'urlopen', side_effect=error) as request:
            with self.assertRaises(HTTPError):
                download_cef_base.download_verified(error.url, 'a' * 40, self.output / 'cef.tar.bz2')
        self.assertEqual(request.call_count, 1)

    def test_interrupted_cef_transfer_can_retry_a_verified_response(self):
        good = b'fixture'
        expected = hashlib.sha1(good).hexdigest()
        archive = self.output / 'cef.tar.bz2'
        with patch.object(download_cef_base, 'urlopen', side_effect=[
                IncompleteRead(b'part', 20), self.download_response(good)]) as request:
            download_cef_base.download_verified('https://example.test/cef.tar.bz2', expected, archive)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(archive.read_bytes(), good)

    def test_workflow_pins_actions_and_only_publishes_tags(self):
        text = (ROOT / '.github/workflows/main.yml').read_text()
        self.assertNotIn('pull_request_target', text)
        self.assertNotIn('UNITY_PASSWORD', text)
        self.assertIn("if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')", text)
        for line in text.splitlines():
            if 'uses:' in line:
                self.assertRegex(line, r'uses: actions/[a-z-]+@[0-9a-f]{40}')


if __name__ == '__main__':
    unittest.main()
