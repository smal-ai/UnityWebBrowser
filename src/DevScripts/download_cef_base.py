"""Download the CEF version pinned by CefGlue, verify it, and extract safely."""

import copy
import hashlib
from http.client import HTTPException
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {'windows64', 'linux64', 'macosx64', 'macosarm64'}


def download_verified(url, expected, archive, attempts=3):
    """Retry read-only downloads, never accepting truncated or mismatched bytes."""
    archive = Path(archive)
    errors = []
    for attempt in range(attempts):
        # A fresh query on retries avoids reusing a damaged CDN cache response.
        address = url if attempt == 0 else url + f'?uwb_sha1={expected}&attempt={attempt + 1}'
        request = Request(address, headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache',
                                            'User-Agent': 'UnityWebBrowser-CI'})
        partial = None
        try:
            with tempfile.NamedTemporaryFile(dir=archive.parent, suffix='.partial', delete=False) as output:
                partial = Path(output.name)
                with urlopen(request, timeout=120) as response:
                    status = response.status
                    length = response.headers.get('Content-Length')
                    if status != 200:
                        raise ValueError(f'Expected a full HTTP 200 response, received {status}.')
                    shutil.copyfileobj(response, output)
            size = partial.stat().st_size
            with partial.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha1').hexdigest()
            if length is not None and size != int(length):
                raise ValueError(f'Truncated CEF download: received {size} bytes, Content-Length {length}; SHA-1 {actual}.')
            if actual != expected:
                raise ValueError(f'CEF download checksum differs: expected {expected}, received {actual}; {size} bytes.')
            if archive.exists():
                raise FileExistsError('Archive appeared during download. No overwrite: ' + str(archive))
            os.replace(partial, archive)
            return
        except HTTPError as error:
            error.close()
            if error.code not in (408, 429) and error.code < 500:
                raise
            errors.append(str(error))
        except (URLError, HTTPException, TimeoutError, ValueError, OSError) as error:
            if isinstance(error, FileExistsError):
                raise
            errors.append(str(error))
        print(f'CEF download attempt {attempt + 1}/{attempts} rejected: {errors[-1]} Partial retained: {partial}', flush=True)
    raise ValueError('CEF download failed all verified attempts. No archive accepted. ' + ' | '.join(errors))


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
        download_verified(url, expected, archive)
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
