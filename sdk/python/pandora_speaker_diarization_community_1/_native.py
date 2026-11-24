"""Load the package's matched native library for the model."""
import ctypes
from pathlib import Path

from pandora_runtime._native import library as _runtime_library


def _library_path():
    selected=Path(__file__).resolve().parent/'libspeaker_diarization.so'
    if not selected.is_file():raise FileNotFoundError('Installed SDK is missing its matched native library')
    return str(selected)


def library():
    path = _library_path()
    _runtime_library()
    return ctypes.CDLL(path)
