# Model setup

## Device requirements

- A compatible Linux device with Aidlux LMS (`sudo lms check` returns true), drivers, DSP firmware and BSP/FastRPC support.
- Python 3.10–3.12 in the Demo environment (see [Install](../README.md#1-install)).
- The SDK includes its QNN runtime libraries. Do not install QNN separately or set QNN paths.

## Targets

Run `cat /sys/devices/soc0/soc_id` on your device and pick its target folder:

| SoC | SoC ID | Target folder |
| --- | --- | --- |
| QCS8550 | 603 | `qcs8550_qnn248` |
| QCS9075 | 676 | `qcs9075_qnn248` |
| QCS8625 | 702 | `qcs8625_qnn248` |
| QCS8275 | 675 | `qcs8275_qnn248` |

## Download

Replace `<target-folder>` with your device's folder from the Targets table.

From the Demo root, in the activated environment, download the shared resources once and then one target:

```bash
hf download APLUX-Pandora/speaker-diarization-community-1 --local-dir models --exclude "targets/*" --exclude "media/*" --exclude README.md --exclude .gitattributes
hf download APLUX-Pandora/speaker-diarization-community-1 --local-dir models --include "targets/<target-folder>/*"
```

Run `hf auth login` first if the repository is private. Keep one target per model folder: to switch devices, move the old `targets/` folder out or download into a new folder.

## Folder layout

After the download, `models/` looks like this:

```text
models/
├── models.json
├── SHA256SUMS
├── config.json
├── decoded-hashes.json
├── cpu/
└── targets/
    └── qcs8550_qnn248/
        ├── embedding.aidem
        └── segmentation.aidem
```

The SDK reads `models.json`, checks that the shared resources and one complete target are present, and reports what is missing. `hf download` verifies each large file's checksum.

At load time, the SDK uses the device's SoC ID to select among the local target folders and checks the expected file sizes. On QCS8275, it uses the QCS8625 target when the QCS8275 folder is absent and the QCS8625 target is available.

## Troubleshooting

| Message or symptom | What to do |
| --- | --- |
| `unsupported device SoC ID -1` | Check that `cat /sys/devices/soc0/soc_id` returns a numeric ID on your device. If the ID is unavailable, check your device's Linux BSP and drivers before retrying. |
| `unsupported device SoC ID` with an ID outside the Targets table | Use a device with a supported SoC from the table. |
| `no model target for device SoC` | The error lists the targets found locally. Download the target for the SoC named in the error using the Download step above. |
| `missing resource`, `empty resource`, or `unexpected size` | Repeat the Download step for the shared files and your device's target with `--force-download`. |
| Model load fails | Check LMS activation, the target folder, and that the required drivers and firmware are present. |
