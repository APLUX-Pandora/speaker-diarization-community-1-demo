"""Verify a separately installed Audio8 runtime without importing or copying it."""
import hashlib
import json
import os
import subprocess
from pathlib import Path


def verify_audio8(root, receipt=None):
    root = Path(root).expanduser().resolve()
    receipt = Path(receipt) if receipt else Path(__file__).with_name('external-dependencies.json')
    expected = json.loads(receipt.read_text())['asr']
    override = os.environ.get('AUDIO8_ASR_LIB')
    if override and Path(override).expanduser().resolve() != (root / 'sdk/lib/libaudio8_asr.so').resolve():
        raise ValueError('AUDIO8_ASR_LIB must reference this qualified Audio8 installation; unset the override')
    for name, digest in expected['files'].items():
        path = root / name
        if not path.is_file():
            raise ValueError('Incomplete Audio8 installation: ' + str(path))
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(block)
        if actual.hexdigest() != digest:
            raise ValueError('Audio8 runtime differs from the qualified ' + expected['sdk_tag'] +
                             ' snapshot: ' + name + '; use the documented external checkout')
    if expected.get('path'):
        # A payload copy is not a component checkout. Fixed commits and file
        # hashes serve separate purposes: identity and runtime integrity.
        try:
            top = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], stderr=subprocess.DEVNULL, text=True).strip()
            commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
            dirty = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError('Audio8 checkout is unavailable; see the meeting setup guide') from error
        if Path(top).resolve() != root or commit != expected['consumer_revision'] or dirty:
            raise ValueError('Use a clean checkout of the Audio8 revision in the meeting setup guide')
    return {'sdk_tag': expected['sdk_tag'], 'consumer_revision': expected['consumer_revision'],
            'verified_files': len(expected['files']), 'bundled': False}


def verify_audio8_sdk(root, module, receipt=None):
    """Match the imported pip SDK to the component, including its native payload."""
    from importlib.metadata import PackageNotFoundError, distribution
    expected = json.loads((Path(receipt) if receipt else Path(__file__).with_name('external-dependencies.json')).read_text())['asr']
    try:
        dist = distribution('audio8-asr-infinite-sdk')
    except PackageNotFoundError as error:
        raise ValueError('Audio8 SDK is not installed; install the external SDK in the active environment') from error
    if dist.version != expected['sdk_tag'].removeprefix('v'):
        raise ValueError('Installed Audio8 SDK version differs from the fixed component')
    package = Path(module.__file__).resolve().parent
    if Path(dist.locate_file('audio8_asr/__init__.py')).resolve() != package/'__init__.py':
        raise ValueError('Audio8 import is shadowed; remove source SDK paths from PYTHONPATH')
    checks = {}
    for name, digest in expected['files'].items():
        if name.startswith('sdk/python/audio8_asr/'):
            checks[package/name.removeprefix('sdk/python/audio8_asr/')] = digest
        elif name == 'sdk/lib/libaudio8_asr.so':
            checks[package/'lib/libaudio8_asr.so'] = digest
        elif name == 'sdk/native-library.json':
            checks[package/'native-library.json'] = digest
    for path, digest in checks.items():
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Installed Audio8 SDK differs from the qualified component: ' + path.name)
    return {'version':dist.version,'verified_installed_files':len(checks)}
