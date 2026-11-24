"""Synchronous ctypes binding. All inference and model protocol run in C++."""
import ctypes as C
import hashlib as _hashlib  # noqa: F401  # Initialize CPython crypto before global vendor plugins.
import os
import ssl as _ssl  # noqa: F401
import threading
from dataclasses import dataclass
from functools import wraps

from ._audio import prepare_audio as _prepare_audio
from ._native import library
from ._paths import resolve_model_package
from ._runtime import Runtime


class RequestOptions(C.Structure):
    _fields_ = [('struct_size', C.c_uint32), ('abi_version', C.c_uint32),
                ('num_speakers', C.c_uint32), ('min_speakers', C.c_uint32),
                ('max_speakers', C.c_uint32), ('reserved_flags', C.c_uint32),
                ('reserved', C.c_uint64 * 4)]


class SegmentStruct(C.Structure):
    _fields_ = [('start_seconds', C.c_double), ('end_seconds', C.c_double),
                ('speaker_id', C.c_uint32), ('kind', C.c_uint32)]


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: int
    kind: str


class DiarizationError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.native_code = code


class DiarizationLoadError(DiarizationError):
    pass


class DiarizationStateError(DiarizationError):
    pass


def _speaker_counts(num, minimum, maximum):
    values = []
    for name, value in [('num_speakers', num), ('min_speakers', minimum), ('max_speakers', maximum)]:
        if value is None:
            values.append(0)
        elif type(value) is not int:
            raise TypeError(name + ' must be a positive integer or None')
        elif not 1 <= value <= 2**31-1:
            raise ValueError(name + ' must be between 1 and 2147483647')
        else:
            values.append(value)
    if not values[0] and values[1] and values[2] and values[1] > values[2]:
        raise ValueError('min_speakers must not exceed max_speakers')
    return tuple(values)


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        self._check_pid()
        with self._lock:
            self._require_open()
            return method(self, *args, **kwargs)
    return call


class Engine:
    @property
    def _handle(self):
        return self._slot.value

    def _check_pid(self):
        if getattr(self,'_pid',os.getpid())!=os.getpid():
            raise DiarizationStateError(-2,'Model inherited through fork; use spawn/exec')
    def __init__(self, models, *, runtime=None):
        self._pid = os.getpid()
        self._lock = threading.RLock()
        self._slot = C.c_void_p()
        self._runtime = runtime
        models = resolve_model_package(models)
        self._lib = library()

        def define(name, result, arguments):
            function = getattr(self._lib, name)
            function.restype = result
            function.argtypes = arguments

        define('sdiar_create_with_runtime', C.c_int32, [C.c_char_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)])
        define('sdiar_destroy_owned', C.c_int32, [C.POINTER(C.c_void_p)])
        define('sdiar_create_error', C.c_char_p, [])
        define('sdiar_reset', C.c_int32, [C.c_void_p])
        define('sdiar_feed', C.c_int32, [C.c_void_p, C.POINTER(C.c_float), C.c_size_t])
        define('sdiar_default_request_options', RequestOptions, [])
        define('sdiar_finish_with_options', C.c_int32, [C.c_void_p, C.POINTER(RequestOptions)])
        define('sdiar_read_segments', C.c_int32, [C.c_void_p, C.POINTER(SegmentStruct), C.c_uint32])
        define('sdiar_last_error', C.c_char_p, [C.c_void_p])
        try:
            if runtime is None:
                code = self._lib.sdiar_create_with_runtime(
                    os.fsencode(models), None, None, C.byref(self._slot))
            else:
                with runtime.borrow_handle() as handle:
                    code = self._lib.sdiar_create_with_runtime(
                        os.fsencode(models), None, handle, C.byref(self._slot))
            if code < 0:
                raise DiarizationLoadError(code,self._lib.sdiar_create_error().decode(errors='replace'))
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if result < 0:
            raise DiarizationError(result, self._lib.sdiar_last_error(self._handle).decode())
        return result

    def _require_open(self):
        self._check_pid()
        if not self._handle:
            raise RuntimeError('Engine is closed')

    @_serialized
    def feed(self, samples):
        """Copy a contiguous native float32 buffer containing mono 16 kHz PCM."""
        view = memoryview(samples)
        if view.format != 'f' or view.ndim != 1 or not view.c_contiguous:
            raise ValueError('Expected a contiguous one-dimensional native float32 buffer')
        array_type = C.c_float * len(view)
        values = array_type.from_buffer_copy(view) if view.readonly else array_type.from_buffer(view)
        self._check(self._lib.sdiar_feed(self._handle, values, len(view)))

    @_serialized
    def finish(self, *, num_speakers=None, min_speakers=None, max_speakers=None):
        counts = _speaker_counts(num_speakers, min_speakers, max_speakers)
        options = self._lib.sdiar_default_request_options()
        options.num_speakers, options.min_speakers, options.max_speakers = counts
        self._check(self._lib.sdiar_finish_with_options(self._handle, C.byref(options)))
        return self.read()

    @_serialized
    def read(self):
        buffer = (SegmentStruct * 256)()
        result = []
        while True:
            count = self._check(self._lib.sdiar_read_segments(self._handle, buffer, len(buffer)))
            if not count:
                return result
            result.extend(Segment(s.start_seconds, s.end_seconds, s.speaker_id,
                                  'standard' if s.kind == 0 else 'exclusive') for s in buffer[:count])

    @_serialized
    def reset(self):
        self._check(self._lib.sdiar_reset(self._handle))

    def close(self):
        self._check_pid()
        with self._lock:
            if self._handle:
                code=self._lib.sdiar_destroy_owned(C.byref(self._slot))
                if code < 0: raise DiarizationStateError(code,'Cannot destroy model')
            self._runtime = None

    def __del__(self):
        if getattr(self,'_pid',None)==os.getpid():
            try: self.close()
            except BaseException: pass  # noqa: BLE001,S110


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class DiarizationResult:
    segments: tuple[SpeakerTurn, ...]
    exclusive_speaker_diarization: tuple[SpeakerTurn, ...]


class DiarizationModel:
    """Separate speakers in one complete recording at a time."""

    @classmethod
    def from_pretrained(cls, model_dir, *, runtime=None):
        if not isinstance(model_dir, (str, os.PathLike)):
            raise TypeError("model_dir must be a local directory path")
        if not os.fspath(model_dir).strip() or "://" in os.fspath(model_dir):
            raise ValueError("model_dir must be a local directory path")
        if runtime is not None and not isinstance(runtime, Runtime):
            raise TypeError("runtime must be Runtime or None")

        model = cls.__new__(cls)
        model._pid = os.getpid()
        model._lock = threading.RLock()
        model._engine = None
        try:
            model._engine = Engine(model_dir, runtime=runtime)
        except (OSError, RuntimeError, ValueError) as error:
            if isinstance(error, DiarizationLoadError):
                raise
            raise DiarizationLoadError(getattr(error, "code", None), str(error)) from error
        return model

    def _check_pid(self):
        if self._pid != os.getpid():
            raise DiarizationStateError(-2, "Model inherited through fork; use spawn or exec")

    def _require_open(self):
        if self._engine is None:
            raise DiarizationStateError(-2, "Model is closed")

    def diarize(self, audio, *, sample_rate=None, channel=None, num_speakers=None,
                min_speakers=None, max_speakers=None):
        self._check_pid()
        with self._lock:
            self._require_open()
            _speaker_counts(num_speakers, min_speakers, max_speakers)
            samples, duration = _prepare_audio(audio, sample_rate=sample_rate, channel=channel)
            engine = self._engine
            try:
                engine.feed(samples)
                segments = engine.finish(num_speakers=num_speakers, min_speakers=min_speakers,
                                         max_speakers=max_speakers)
            except DiarizationError as error:
                if error.native_code == -1:
                    self._reset()
                else:
                    self.close()
                raise
            except BaseException:
                self.close()
                raise
            self._reset()
            return DiarizationResult(
                _speaker_turns(segments, "standard", duration),
                _speaker_turns(segments, "exclusive", duration),
            )

    def _reset(self):
        try:
            self._engine.reset()
        except BaseException:
            self.close()
            raise

    def close(self):
        self._check_pid()
        with self._lock:
            if self._engine is not None:
                self._engine.close()
                self._engine = None

    def __enter__(self):
        self._check_pid()
        with self._lock:
            self._require_open()
        return self

    def __exit__(self, *_):
        self.close()


def _speaker_turns(segments, kind, duration):
    turns = []
    for segment in segments:
        if segment.kind == kind:
            start = max(0.0, segment.start)
            end = min(duration, segment.end)
            if start < end:
                turns.append(SpeakerTurn(start, end, f"SPEAKER_{segment.speaker:02d}"))
    return tuple(sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker)))
