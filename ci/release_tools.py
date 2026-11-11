"""Build verifiable Unity package tarballs without a Unity licence or npm token."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = ['UnityWebBrowser', 'UnityWebBrowser.Engine.Cef', 'UnityWebBrowser.Communication.Pipes']
NATIVE_PACKAGES = {
    'win-x64': 'UnityWebBrowser.Engine.Cef.Win-x64',
    'linux-x64': 'UnityWebBrowser.Engine.Cef.Linux-x64',
    'osx-x64': 'UnityWebBrowser.Engine.Cef.MacOS-x64',
    'osx-arm64': 'UnityWebBrowser.Engine.Cef.MacOS-arm64',
}
TARGETS = ['core', *NATIVE_PACKAGES]
IGNORED = {'.git', '.gitignore', '.npmignore', '.npmrc', '.DS_Store', '__pycache__', 'node_modules'}


def sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def write_once(path, data):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Existing output differs. No overwrite: ' + str(path))
        return
    with path.open('xb') as output:
        output.write(data)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def architecture(path):
    with Path(path).open('rb') as source:
        header = source.read(64)
        if header[:2] == b'MZ':
            source.seek(struct.unpack_from('<I', header, 0x3c)[0])
            pe = source.read(6)
            if pe[:4] != b'PE\x00\x00':
                raise ValueError('Invalid PE signature: ' + str(path))
            return {0x8664: 'win-x64', 0xaa64: 'win-arm64'}[struct.unpack_from('<H', pe, 4)[0]]
        if header[:4] == b'\x7fELF' and header[4:6] == b'\x02\x01':
            return {62: 'linux-x64', 183: 'linux-arm64'}[struct.unpack_from('<H', header, 18)[0]]
        if header[:4] == b'\xcf\xfa\xed\xfe':
            return {0x01000007: 'osx-x64', 0x0100000c: 'osx-arm64'}[struct.unpack_from('<I', header, 4)[0]]
    raise ValueError('Unrecognised native binary: ' + str(path))


def verify_engine(directory, target):
    directory = Path(directory)
    if target == 'win-x64':
        executable = directory / 'UnityWebBrowser.Engine.Cef.exe'
        binaries = [executable, directory / 'UnityWebBrowser.Engine.Cef.dll', directory / 'libcef.dll']
    elif target == 'linux-x64':
        executable = directory / 'UnityWebBrowser.Engine.Cef'
        binaries = [executable, directory / 'libcef.so']
    else:
        app = directory / 'UnityWebBrowser.Engine.Cef.app/Contents'
        executable = app / 'MacOS/UnityWebBrowser.Engine.Cef'
        framework = app / 'Frameworks'
        binaries = [executable, framework / 'Chromium Embedded Framework.framework/Chromium Embedded Framework']
        for suffix in ['', ' (GPU)', ' (Plugin)', ' (Renderer)']:
            name = 'UnityWebBrowser.Engine.Cef.SubProcess' + suffix
            binaries.append(framework / (name + '.app/Contents/MacOS/' + name))
        if not (app / 'Info.plist').is_file():
            raise ValueError('The macOS application has no Info.plist.')
    records = []
    for binary in binaries:
        actual = architecture(binary)
        if actual != target:
            raise ValueError(f'Wrong architecture for {binary}: {actual}, expected {target}')
        records.append({'path': binary.relative_to(directory).as_posix(), 'architecture': actual, 'sha256': sha256(binary)})
    if target in ('win-x64', 'linux-x64'):
        for name in ['icudtl.dat', 'resources.pak', 'locales/en-US.pak', 'LICENSE.CEF.txt']:
            if not (directory / name).is_file():
                raise ValueError('Missing CEF runtime resource: ' + name)
    environment = os.environ.copy()
    if target == 'linux-x64':
        environment['LD_LIBRARY_PATH'] = str(directory.resolve()) + os.pathsep + environment.get('LD_LIBRARY_PATH', '')
    result = subprocess.run([str(executable.resolve()), '--help'], cwd=executable.parent,
                            env=environment, capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    if result.returncode or 'help' not in output.lower():
        raise ValueError(f'Engine CLI smoke test failed ({result.returncode}):\n{output[-8000:]}')
    return {'native_files': records, 'cli_help_exit_code': result.returncode}


def package_paths(directory):
    directory = Path(directory).resolve()

    def walk(parent):
        for child in sorted(parent.iterdir(), key=lambda path: path.name):
            if child.name in IGNORED or child.suffix == '.pyc':
                continue
            if child.is_symlink():
                if not child.resolve().is_relative_to(directory) or not child.exists():
                    raise ValueError('Package link escapes its directory or is broken: ' + str(child))
                yield child
            elif child.is_dir():
                yield child
                yield from walk(child)
            elif child.is_file():
                yield child
            else:
                raise ValueError('Unsupported package entry: ' + str(child))

    yield from walk(directory)


def pack_package(directory, output):
    directory = Path(directory).resolve()
    output = Path(output)
    package = json.loads((directory / 'package.json').read_text())
    if not re.fullmatch(r'[a-z0-9.-]+', package['name']) or not re.fullmatch(r'[0-9A-Za-z.+-]+', package['version']):
        raise ValueError('Unsafe package name or version.')
    archive = output / f"{package['name']}-{package['version']}.tgz"
    partial = archive.with_suffix('.tgz.partial')
    if partial.exists():
        raise ValueError('Unfinished package retained. Inspect before retrying: ' + str(partial))
    with partial.open('xb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT, dereference=False) as bundle:
                for path in [directory, *package_paths(directory)]:
                    name = 'package' if path == directory else 'package/' + path.relative_to(directory).as_posix()
                    info = bundle.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    info.mtime = 0
                    info.pax_headers = {}
                    info.mode = 0o755 if info.isdir() or info.mode & stat.S_IXUSR else 0o644
                    if info.isfile():
                        with path.open('rb') as source:
                            bundle.addfile(info, source)
                    else:
                        bundle.addfile(info)
    if archive.exists():
        if sha256(archive) != sha256(partial):
            raise ValueError('Existing package differs. Partial retained: ' + str(partial))
        partial.unlink()
    else:
        partial.rename(archive)
    return {'name': archive.name, 'package': package['name'], 'version': package['version'],
            'bytes': archive.stat().st_size, 'sha256': sha256(archive)}


def pack(target, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    metadata = {'target': target, 'source_sha': git('rev-parse', 'HEAD')}
    if target == 'core':
        shared = ROOT / 'src/Packages/UnityWebBrowser/Plugins/VoltstroStudios.UnityWebBrowser.Shared.dll'
        if not shared.is_file() or shared.read_bytes()[:2] != b'MZ':
            raise ValueError('Build the shared Unity DLL before packaging core.')
        folders = CORE_PACKAGES
    else:
        folders = [NATIVE_PACKAGES[target]]
        metadata['validation'] = verify_engine(ROOT / 'src/Packages' / folders[0] / 'Engine~', target)
        version_file = ROOT / 'src/ThirdParty/CefGlue/CefGlue/Interop/version.g.cs'
        metadata['cef_version'] = re.search(r'CEF_VERSION = "([^"]+)"', version_file.read_text()).group(1)
        metadata['cefglue_sha'] = git('-C', 'src/ThirdParty/CefGlue', 'rev-parse', 'HEAD')
    metadata['packages'] = [pack_package(ROOT / 'src/Packages' / folder, output) for folder in folders]
    write_once(output / ('build-' + target + '.json'), json_bytes(metadata))
    print(json.dumps(metadata, indent=2))


def validate_record(directory, record):
    if Path(record['name']).name != record['name'] or not record['name'].endswith('.tgz'):
        raise ValueError('Invalid package asset name.')
    path = Path(directory) / record['name']
    if path.stat().st_size != record['bytes'] or sha256(path) != record['sha256']:
        raise ValueError('Package hash/size differs: ' + record['name'])
    if record['bytes'] >= 2 * 1024 ** 3:
        raise ValueError('Package exceeds the GitHub release asset limit: ' + record['name'])
    with tarfile.open(path, 'r:gz') as archive:
        source = archive.extractfile('package/package.json')
        if source is None:
            raise ValueError('Package manifest is absent.')
        package = json.load(source)
        if package['name'] != record['package'] or package['version'] != record['version']:
            raise ValueError('Packaged identity differs: ' + record['name'])
        names = archive.getnames()
        if any(not name.startswith('package/') and name != 'package' for name in names):
            raise ValueError('Invalid package archive root.')
        if record['package'] == 'dev.voltstro.unitywebbrowser':
            if 'package/Plugins/VoltstroStudios.UnityWebBrowser.Shared.dll' not in names:
                raise ValueError('Core package is missing its compiled shared DLL.')


def validate_manifest(directory, manifest):
    if manifest.get('schema_version') != 1 or not re.fullmatch(r'[0-9a-f]{40}', manifest['source_sha']):
        raise ValueError('Invalid release manifest schema or source identity.')
    packages = manifest['packages']
    expected = {'dev.voltstro.' + folder.lower().replace('-', '.')
                for folder in [*CORE_PACKAGES, *NATIVE_PACKAGES.values()]}
    if len(packages) != 7 or {record['package'] for record in packages} != expected:
        raise ValueError('A release requires exactly the seven expected Unity packages.')
    if len({record['name'] for record in packages}) != 7 or len({record['version'] for record in packages}) != 1:
        raise ValueError('Release assets collide or package versions differ.')
    for record in packages:
        validate_record(directory, record)


def assemble(output):
    output = Path(output)
    expected_sha = git('rev-parse', 'HEAD')
    builds = []
    for target in TARGETS:
        build = json.loads((output / ('build-' + target + '.json')).read_text())
        if build['target'] != target or build['source_sha'] != expected_sha:
            raise ValueError('Mixed build source identities: ' + target)
        if target != 'core' and build['validation']['cli_help_exit_code'] != 0:
            raise ValueError('Native smoke test did not pass: ' + target)
        builds.append(build)
    if len({build['cef_version'] for build in builds if build['target'] != 'core'}) != 1:
        raise ValueError('CEF versions differ between platforms.')
    if len({build['cefglue_sha'] for build in builds if build['target'] != 'core'}) != 1:
        raise ValueError('CefGlue source revisions differ between platforms.')
    manifest = {'schema_version': 1, 'source_sha': expected_sha,
                'packages': sorted([record for build in builds for record in build['packages']], key=lambda record: record['name']),
                'builds': builds}
    validate_manifest(output, manifest)
    if {path.name for path in output.glob('*.tgz')} != {record['name'] for record in manifest['packages']}:
        raise ValueError('Unexpected or missing package archives.')
    write_once(output / 'release-manifest.json', json_bytes(manifest))
    checksums = [f"{record['sha256']}  {record['name']}\n" for record in manifest['packages']]
    checksums.append(sha256(output / 'release-manifest.json') + '  release-manifest.json\n')
    write_once(output / 'SHA256SUMS', ''.join(checksums).encode('ascii'))
    print(f"Verified seven packages from {expected_sha} across Windows, Linux and macOS.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['pack', 'assemble'])
    parser.add_argument('--target', choices=TARGETS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'pack':
        if not args.target:
            parser.error('pack requires --target')
        pack(args.target, args.output)
    else:
        assemble(args.output)


if __name__ == '__main__':
    main()
