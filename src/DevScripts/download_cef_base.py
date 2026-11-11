"""Download the CEF version pinned by CefGlue, verify it, and extract safely."""

import copy
import hashlib
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {'windows64', 'linux64', 'macosx64', 'macosarm64'}


def extract_cef(archive, destination, archive_root):
    with tarfile.open(archive, 'r:bz2') as source:
        members = []
        for original in source.getmembers():
            if original.name.rstrip('/') == archive_root:
                continue
            if not original.name.startswith(archive_root + '/'):
                raise ValueError('CEF archive contains an unexpected root: ' + original.name)
            member = copy.copy(original)
            member.name = original.name[len(archive_root) + 1:]
            members.append(member)
        source.extractall(destination, members=members, filter='data')


def download_cef(os_platform: str) -> str:
    if os_platform not in PLATFORMS:
        raise ValueError('Unsupported CEF platform: ' + os_platform)
    version_file = ROOT / 'ThirdParty/CefGlue/CefGlue/Interop/version.g.cs'
    match = re.search(r'CEF_VERSION = "([^"]+)"', version_file.read_text())
    if match is None:
        raise ValueError('Pinned CefGlue source does not declare a CEF version.')
    version = match.group(1)
    archive_root = f'cef_binary_{version}_{os_platform}_minimal'
    cache = ROOT / 'ThirdParty/Libs/cef'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / (archive_root + '.tar.bz2')
    url = 'https://cef-builds.spotifycdn.com/' + archive.name
    # CEF publishes SHA-1 sidecars. Release packages additionally use SHA-256.
    with urlopen(url + '.sha1', timeout=120) as response:
        expected = response.read(256).decode('ascii').strip().split()[0].lower()
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('CEF checksum sidecar is invalid.')
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=cache, suffix='.partial', delete=False) as output:
            partial = Path(output.name)
            with urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, output)
        with partial.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha1').hexdigest()
        if actual != expected:
            raise ValueError('CEF download checksum differs. Partial retained: ' + str(partial))
        os.replace(partial, archive)
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha1').hexdigest()
    if actual != expected:
        raise ValueError('Existing CEF archive checksum differs. No overwrite: ' + str(archive))
    destination = cache / os_platform
    stamp = destination / '.uwb-cef-archive-sha1'
    if destination.exists():
        if stamp.is_file() and stamp.read_text().strip() == expected:
            return str(destination)
        raise ValueError('Existing CEF directory is not this verified extraction. No overwrite: ' + str(destination))
    with tempfile.TemporaryDirectory(dir=cache, prefix=os_platform + '-') as temporary:
        extracted = Path(temporary) / 'extracted'
        extracted.mkdir()
        extract_cef(archive, extracted, archive_root)
        (extracted / stamp.name).write_text(expected + '\n')
        os.rename(extracted, destination)
    print(f'Verified and extracted CEF {version} for {os_platform}.')
    return str(destination)
