# Web Demo

After [installation and model download](../README.md), run this from the Demo
root with the active Python environment:

```bash
bash demo/run.sh --port 8766
```

Open the device address on port 8766. Choose the bundled audio or upload WAV,
FLAC, OGG or MP4 (H.264 with AAC). Analyze the recording, select the standard
or exclusive timeline, and click a turn to play it. The Web Demo supports
recordings up to 30 minutes.

The server stores uploaded recordings and analysis results under its local
state directory. Use `--state-dir` to select a different location and
`--host 127.0.0.1` for local-only access. Press Ctrl+C to stop and wait for any
in-flight analysis to release the model.

Optional live transcription requires a compatible Audio8 ASR Infinite release. See the [meeting guide](MEETING.md).
