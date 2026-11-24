# Examples

## Overview

| Example | What it shows |
| --- | --- |
| [python/diarize.py](python/diarize.py) | Separate speakers in the bundled meeting audio. |
| [c/runtime.c](c/runtime.c) | Separate speakers in the bundled meeting audio through the C ABI. |

## Before you run

Complete [Install](../README.md#1-install) and [Get the model](../README.md#2-get-the-model). Run every command from the Demo root in the activated environment. Stop the Web Demo first; examples and the Web Demo share the NPU.

## Python examples

### diarize.py

```bash
python examples/python/diarize.py
```

Output (first three turns):

```text
0.031–7.743 s SPEAKER_01
0.858–1.364 s SPEAKER_00
3.878–4.030 s SPEAKER_00
```

## C example

### runtime.c

Build against the headers and prebuilt library in `sdk/`:

```bash
runtime_dir=$(python -c "import pathlib,pandora_runtime; print(pathlib.Path(pandora_runtime.__file__).parent)")
cc -std=c99 -Wall -Wextra -Werror -I sdk/include examples/c/runtime.c -L sdk/lib -L"$runtime_dir" -Wl,--no-as-needed -l:libpandora_runtime.so.5 -Wl,--as-needed -Wl,-rpath,"$runtime_dir" -lspeaker_diarization -Wl,-rpath,'$ORIGIN/../../sdk/lib' -o examples/c/runtime
examples/c/runtime models
```

Output (first three turns):

```text
0.031–7.743 s SPEAKER_01
0.858–1.364 s SPEAKER_00
3.878–4.030 s SPEAKER_00
```

See [C ABI](../docs/C_ABI.md) for ownership and error handling.
