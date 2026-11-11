"""Publish a complete tag release without overwriting existing release assets."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from release_tools import json_bytes, sha256, validate_manifest


def api(method, path, payload=None, missing_ok=False, raw=False):
    args = ['gh', 'api', '--method', method, path]
    if payload is not None:
        args += ['--input', '-']
    if raw:
        args += ['-H', 'Accept: application/octet-stream']
    result = subprocess.run(args, input=json_bytes(payload) if payload is not None else None, capture_output=True)
    if result.returncode:
        if missing_ok and b'(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout if raw else json.loads(result.stdout)


def release_assets(manifest, directory):
    records = {record['name']: {'sha256': record['sha256'], 'bytes': record['bytes']}
               for record in manifest['packages']}
    for name in ['release-manifest.json', 'SHA256SUMS']:
        path = Path(directory) / name
        records[name] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
    return records


def verify_remote_assets(release, expected):
    actual = {asset['name']: asset for asset in release['assets']}
    for name, record in expected.items():
        asset = actual.get(name)
        if asset is None or asset['state'] != 'uploaded' or asset['size'] != record['bytes']:
            raise ValueError('Release asset is absent, incomplete or has a different size: ' + name)
        if asset.get('digest') != 'sha256:' + record['sha256']:
            raise ValueError('Release asset SHA-256 differs or is unavailable: ' + name)


def publish(directory, tag):
    directory = Path(directory)
    manifest = json.loads((directory / 'release-manifest.json').read_text())
    validate_manifest(directory, manifest)
    sums = ''.join(f"{record['sha256']}  {record['name']}\n" for record in manifest['packages'])
    sums += sha256(directory / 'release-manifest.json') + '  release-manifest.json\n'
    if (directory / 'SHA256SUMS').read_bytes() != sums.encode('ascii'):
        raise ValueError('SHA256SUMS differs from the verified release manifest.')
    version = manifest['packages'][0]['version']
    if not re.fullmatch('v' + re.escape(version) + r'(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?', tag):
        raise ValueError('Release tag must match the package version, optionally with a prerelease suffix.')
    source_sha = manifest['source_sha']
    actual_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    tag_sha = subprocess.run(['git', 'rev-parse', f'refs/tags/{tag}^{{}}'], capture_output=True, text=True, check=True).stdout.strip()
    if source_sha != actual_sha or tag_sha != source_sha:
        raise ValueError('Tag, checkout and release build source do not match.')
    subprocess.run(['git', 'merge-base', '--is-ancestor', source_sha, 'origin/main'], check=True)
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+', repo):
        raise ValueError('Invalid GitHub repository identity.')
    endpoint = 'repos/' + repo + '/releases'
    release = api('GET', endpoint + '/tags/' + tag, missing_ok=True)
    expected = release_assets(manifest, directory)
    if release is not None and not release['draft']:
        assets = {asset['name']: asset for asset in release['assets']}
        asset = assets.get('release-manifest.json')
        if asset is None:
            raise ValueError('Existing published release has no source manifest. No overwrite.')
        original_bytes = api('GET', f"repos/{repo}/releases/assets/{asset['id']}", raw=True)
        original = json.loads(original_bytes)
        if original.get('source_sha') != source_sha:
            raise ValueError('Existing published release is from another source. No overwrite.')
        # A retry may produce new archive bytes. Retain the original complete
        # published build, not a mixture of outputs from separate attempts.
        original_expected = {record['name']: {'sha256': record['sha256'], 'bytes': record['bytes']}
                             for record in original['packages']}
        original_expected['release-manifest.json'] = {'sha256': hashlib.sha256(original_bytes).hexdigest(), 'bytes': len(original_bytes)}
        sums = ''.join(f"{record['sha256']}  {record['name']}\n" for record in original['packages'])
        sums += original_expected['release-manifest.json']['sha256'] + '  release-manifest.json\n'
        original_expected['SHA256SUMS'] = {'sha256': hashlib.sha256(sums.encode('ascii')).hexdigest(), 'bytes': len(sums.encode('ascii'))}
        if set(original_expected) != set(expected):
            raise ValueError('Existing published asset identities differ. No overwrite.')
        verify_remote_assets(release, original_expected)
        print('Release already complete and verified: ' + release['html_url'])
        return
    if release is None:
        notes = f'''Native CEF engine builds and Unity packages from `{source_sha}`.

| Platform | Native engine |
| --- | --- |
| Windows | x64 |
| Linux | x64, built on Ubuntu 22.04 |
| macOS | Intel x64 and Apple Silicon ARM64 |

Install the `.tgz` files using Unity Package Manager's **Install package from tarball** command.
The core tarball includes its compiled shared DLL. Install the CEF integration package
and the native package for your target platform. The pipes package is optional.
Verify downloads using `SHA256SUMS` and inspect `release-manifest.json` for build provenance.

Windows ARM64 Unity uses the Windows x64 CEF engine under emulation, not a native
ARM64 CEF build. The tested local-HTML profile explicitly disables the sandbox.
See [ARM64 setup](https://github.com/{repo}/blob/{tag}/docs/articles/user/windows-arm64.md).

These distribution builds are not release-signed or notarised. They are not
signed installers or
Unity game executables. CI checks architectures and CLI startup, not desktop rendering.
'''
        release = api('POST', endpoint, {'tag_name': tag, 'target_commitish': source_sha,
                      'name': 'UnityWebBrowser ' + tag, 'body': notes, 'draft': True, 'prerelease': '-' in tag})
    existing = {asset['name']: asset for asset in release['assets']}
    for name, record in expected.items():
        if name in existing:
            verify_remote_assets({'assets': [existing[name]]}, {name: record})
        else:
            # Never use --clobber. After an uncertain upload, a retry inspects the
            # server-side digest rather than replaying or replacing the asset.
            subprocess.run(['gh', 'release', 'upload', tag, str(directory / name), '--repo', repo], check=True)
    release = api('GET', endpoint + '/' + str(release['id']))
    verify_remote_assets(release, expected)
    release = api('PATCH', endpoint + '/' + str(release['id']), {'draft': False})
    print('Published complete verified release: ' + release['html_url'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    publish(args.directory, args.tag)


if __name__ == '__main__':
    main()
