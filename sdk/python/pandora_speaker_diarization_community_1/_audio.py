"""Editable file/PCM preparation; no model inference or automatic segmentation."""
from __future__ import annotations

import math
import os

import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16000


def rate(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("sample_rate must be a positive integer in Hz")
    if value <= 0:
        raise ValueError("sample_rate must be positive")
    return value


def mono(pcm, channel=None):
    """Channel-last floating PCM only. Never guess encoding or axis order."""
    if not isinstance(pcm, np.ndarray):
        raise TypeError("PCM must be a NumPy float32 or float64 array")
    if pcm.dtype not in (np.dtype("float32"), np.dtype("float64")):
        raise TypeError("PCM must be float32 or float64; integer PCM is unsupported")
    if pcm.ndim not in (1, 2) or (pcm.ndim == 2 and pcm.shape[1] == 0):
        raise ValueError("PCM shape must be (samples,) or (samples, channels)")
    count = 1 if pcm.ndim == 1 else pcm.shape[1]
    if not np.isfinite(pcm).all() or np.any(np.abs(pcm) > 1):
        raise ValueError("PCM must contain finite normalized samples in [-1, 1]")
    if channel is not None:
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise TypeError("channel must be a zero-based integer or None")
        if not 0 <= channel < count:
            raise ValueError("Selected channel is out of range")
        result = pcm if pcm.ndim == 1 else pcm[:, channel]
    elif count == 1:
        result = pcm if pcm.ndim == 1 else pcm[:, 0]
    elif count == 2:
        result = pcm.mean(axis=1, dtype=np.float64)
    else:
        raise ValueError("More than two channels require explicit channel selection")
    return np.ascontiguousarray(result, dtype=np.float32)


def prepare_audio(audio, *, sample_rate=None, channel=None):
    """Return model-rate mono PCM and original input duration.

    Extend this function to add other file decoders. The native SDK only sees
    prepared floating samples, so format changes do not require a library build.
    """
    if isinstance(audio, (str, os.PathLike)):
        import soundfile as sf
        path = os.fspath(audio)
        if not path or "://" in path:
            raise ValueError("audio must be a local file path")
        with sf.SoundFile(path) as stream:
            source_rate = int(stream.samplerate)
            if sample_rate is not None and rate(sample_rate) != source_rate:
                raise ValueError("sample_rate conflicts with the audio file metadata")
            pcm = stream.read(dtype="float32", always_2d=True)
    else:
        if sample_rate is None:
            raise ValueError("PCM input requires the original sample_rate")
        source_rate = rate(sample_rate)
        pcm = audio
    prepared = mono(pcm, channel)
    duration = len(prepared) / source_rate
    if not len(prepared):
        raise ValueError("Audio must contain samples")
    divisor = math.gcd(source_rate, TARGET_RATE)
    up, down = TARGET_RATE // divisor, source_rate // divisor
    if max(up, down) > 100000:
        raise ValueError("Source rate ratio requires an oversized FIR; resample explicitly before input")
    result = resample_poly(prepared, up, down).astype(np.float32) if up != down else prepared.copy()
    return result, duration
