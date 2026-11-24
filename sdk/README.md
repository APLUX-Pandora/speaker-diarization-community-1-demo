# Speaker Diarization Community-1 SDK

Separate speakers in complete recordings on the device NPU.

## What's included

| Path | Content |
| --- | --- |
| `python/pandora_speaker_diarization_community_1/` | Python package (`from pandora_speaker_diarization_community_1 import DiarizationModel`) |
| `include/` | C header |
| `lib/` | Prebuilt native library with its QNN runtime |
| `SHA256SUMS` | Checksums of the shipped files |

The shared hardware Runtime wheel is in `wheels/`.

## Install

In your application's activated Python 3.10–3.12 environment, from the Demo root:

```bash
python -m pip install sdk/wheels/pandora_runtime-*.whl
python -m pip install ./sdk
```

The device must pass `sudo lms check`. The SDK does not install LMS or drivers and does not download models; see [Get the model](../README.md#2-get-the-model).

## Usage

```python
from pandora_speaker_diarization_community_1 import DiarizationModel

with DiarizationModel.from_pretrained("models") as model:
    result = model.diarize("examples/audio/meeting.wav")
    for segment in result.segments:
        print(segment.start, segment.end, segment.speaker)
```

## Documentation

- [Python API](PYTHON_API.md)
- [Runtime](RUNTIME_API.md)
- [C ABI](../docs/C_ABI.md)
- [Examples](../examples/README.md)
- [Licensing](../LICENSING.md)
