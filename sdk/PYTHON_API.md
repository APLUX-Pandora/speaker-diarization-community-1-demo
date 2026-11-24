# Python API

`pandora_speaker_diarization_community_1` · Separate speakers in complete recordings on the device NPU.

## Quick start

```python
from pandora_speaker_diarization_community_1 import DiarizationModel

with DiarizationModel.from_pretrained("models") as model:
    result = model.diarize("examples/audio/meeting.wav")
    for segment in result.segments:
        print(segment.start, segment.end, segment.speaker)
```

## API overview

| Name | Signature | Returns | Purpose |
| --- | --- | --- | --- |
| `DiarizationModel.from_pretrained` | `(model_dir, *, runtime=None)` | `DiarizationModel` | Load a local model directory. |
| `diarize` | `(audio, *, sample_rate=None, channel=None, num_speakers=None, min_speakers=None, max_speakers=None)` | `DiarizationResult` | Separate speakers in one complete recording. |
| `close` | `()` | `None` | Release the model. Repeated calls are safe. |

## Parameters

### `from_pretrained`

| Parameter | Type | Default | Range / unit | Meaning |
| --- | --- | --- | --- | --- |
| `model_dir` | path | required | Local directory | Read a complete model package. |
| `runtime` | `Runtime` | `None` | Open runtime or `None` | Omit to use a model-owned runtime with the defaults in the advanced guide. |

### `diarize`

| Parameter | Type | Default | Range / unit | Meaning |
| --- | --- | --- | --- | --- |
| `audio` | path or NumPy array | required | WAV, FLAC, MP3, or float32/float64 PCM in [-1, 1] | Audio as a file or a channel-last array of shape `(samples,)` or `(samples, channels)`. |
| `sample_rate` | int | `None` | Positive Hz | Required for arrays. Files use their own rate and any explicit rate must match. |
| `channel` | int | `None` | Zero-based | Select a channel. By default mono is used directly, stereo is averaged, and more channels require a selection. |
| `num_speakers` | int | `None` | 1–2147483647 | Request a fixed count and take precedence over the bounds. |
| `min_speakers` | int | `None` | 1–2147483647 | Request a lower bound on clustering. |
| `max_speakers` | int | `None` | 1–2147483647 | Request an upper bound on clustering. |

With no fixed count, a supplied minimum must be at most the maximum. The count
controls guide clustering. A recording with insufficient speech evidence can
produce fewer labels. If clustering has at least two clean embeddings, a count
that exceeds the available training rows raises an error. The SDK resamples to 16 kHz.

## Results and cleanup

### `DiarizationResult`

| Field | Type | Meaning |
| --- | --- | --- |
| `segments` | tuple of `SpeakerTurn` | Turns in start-time order. Turns from different speakers can overlap. |
| `exclusive_speaker_diarization` | tuple of `SpeakerTurn` | Model extension with zero or one speaker at any instant. |

Each `SpeakerTurn` has `start` and `end` (seconds from the beginning of input,
with an exclusive end) and `speaker` (an anonymous recording-local string such
as `SPEAKER_00`). Silence returns empty tuples.

### Errors

| Exception | When |
| --- | --- |
| `TypeError`, `ValueError` | Input type, shape, sample rate, channel selection, sample values or count options are invalid. |
| `soundfile.LibsndfileError` | The input file is missing or decoding fails. |
| `DiarizationLoadError` | A model resource, runtime library, activation or model initialization failed. |
| `DiarizationError` | The native request was rejected or inference failed. The error includes `native_code`. |
| `DiarizationStateError` | The model is closed or belongs to a different process. |

### Lifecycle

Use a context manager or call `close()`. Results use immutable Python values and
remain readable after another request or after close. Calls on the same model
are serialized. An input rejected during preprocessing leaves the model ready
for the next recording. After a backend failure, open a new model.

## Advanced

See [Runtime](RUNTIME_API.md) for hardware settings and [Examples](../examples/README.md) for runnable programs.
