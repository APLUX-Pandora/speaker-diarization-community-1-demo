# Speaker Diarization Community-1

<p align="center">
  <a href="https://huggingface.co/APLUX-Pandora/speaker-diarization-community-1"><img src="https://img.shields.io/badge/Hugging%20Face-SPEAKER--DIARIZATION--COMMUNITY--1-E3B800?style=for-the-badge&amp;logo=huggingface&amp;logoColor=FFD21E&amp;labelColor=555555" alt="Hugging Face: Speaker Diarization Community-1"></a>
  <a href="https://github.com/APLUX-Pandora/speaker-diarization-community-1-demo"><img src="https://img.shields.io/badge/GitHub-SPEAKER--DIARIZATION--COMMUNITY--1-000000?style=for-the-badge&amp;logo=github&amp;logoColor=white&amp;labelColor=555555" alt="GitHub: Speaker Diarization Community-1"></a>
</p>

On-device speaker diarization returns time intervals for anonymous speakers in recordings.

Based on [Speaker Diarization Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).

**Demo recording:** reserved for a post-release recording.

## 1. Install

Clone the Demo and enter its root directory:

```bash
git clone https://github.com/APLUX-Pandora/speaker-diarization-community-1-demo.git
cd speaker-diarization-community-1-demo
```

### Prepare the device

Use a compatible Linux device with Aidlux LMS and the required drivers, DSP firmware and BSP/FastRPC support. The SDK includes its QNN runtime libraries; no separate QNN installation or path configuration is required.

Check the activation:

```bash
sudo lms check
```

Continue only when it returns true.

Install the system packages the Demo needs:

```bash
sudo apt install python3-venv libsndfile1 ffmpeg
```

### Install the Demo and SDK

Use Python 3.10–3.12. Create and activate the Demo environment, then install:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

This installs the SDK, the Web Demo and the Hugging Face CLI. To use only the SDK in your own application, see the [SDK installation](sdk/README.md#install).

## 2. Get the model

Run `cat /sys/devices/soc0/soc_id` on your device. If its SoC is different from the example, replace `qcs8550_qnn248` with the matching folder from the [Targets table](docs/MODEL_SETUP.md#targets).

Download the shared resources and the target for your device. This example uses QCS8550:

```bash
hf download APLUX-Pandora/speaker-diarization-community-1 --local-dir models --exclude "targets/*" --exclude "media/*" --exclude README.md --exclude .gitattributes
hf download APLUX-Pandora/speaker-diarization-community-1 --local-dir models --include "targets/qcs8550_qnn248/*"
```

For a private repository, run `hf auth login` first. Available targets and the folder layout are in [Model setup](docs/MODEL_SETUP.md).

## 3. Run the Web Demo

```bash
bash demo/run.sh --port 8766
```

Open `http://<device-ip>:8766/` and select the bundled meeting audio or upload a recording. Stop it with `Ctrl+C`. See the [Web Demo guide](demo/README.md).

## 4. Python Usage

In the same environment, from the Demo root:

```python
from pandora_speaker_diarization_community_1 import DiarizationModel

with DiarizationModel.from_pretrained("models") as model:
    result = model.diarize("examples/audio/meeting.wav")
    for segment in result.segments:
        print(segment.start, segment.end, segment.speaker)
```

More in the [Python API](sdk/PYTHON_API.md) and [examples](examples/README.md).

## Documentation

- [Model setup](docs/MODEL_SETUP.md): device requirements, targets, download and folder layout
- [Python API](sdk/PYTHON_API.md): every public class, method and parameter
- [Runtime](sdk/RUNTIME_API.md): optional hardware settings
- [C ABI](docs/C_ABI.md): native integration
- [Examples](examples/README.md): runnable Python and C examples

- [Meeting workflow](demo/MEETING.md): optional live transcription and speaker analysis, pending a compatible Infinite release
- [Media attribution](docs/MEDIA_ATTRIBUTION.md): source and license of the example recording

## License

See [LICENSING.md](LICENSING.md), [NOTICE](NOTICE) and [LICENSES](LICENSES/).
