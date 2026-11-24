# C ABI

`sdk/include/speaker_diarization.h` · `sdk/lib/libspeaker_diarization.so` · Separate speakers in complete recordings.

## Build and link

Activate your application's Python environment and follow the [SDK installation steps](../sdk/README.md#install) to install the bundled Runtime and SDK.

From the Demo root, compile against the shipped header and library; the library carries its QNN runtime:

```bash
runtime_dir=$(python -c "import pathlib,pandora_runtime; print(pathlib.Path(pandora_runtime.__file__).parent)")
cc -std=c99 -I sdk/include your_app.c -L sdk/lib -L"$runtime_dir" -Wl,--no-as-needed -l:libpandora_runtime.so.5 -Wl,--as-needed -Wl,-rpath,"$runtime_dir" -lspeaker_diarization -Wl,-rpath,'$ORIGIN/sdk/lib' -o your_app
```

The Runtime wheel supplies the shared C library. For deployment without Python, put that library where the system loader can find it.

## Lifecycle

```text
create → feed PCM → finish → read_segments until empty → destroy
                             reset → feed the next recording
```

Pass a model directory containing its shared resources and a complete target.
Check every status. After a rejected request, reset before feeding another
recording. If the backend fails, release the model and open a new one.

## Functions

| Function | Purpose | Returns |
| --- | --- | --- |
| `sdiar_version` | Get the SDK identifier. | Borrowed static string. |
| `sdiar_default_options` | Prepare model options with initialized ABI fields. | Options by value. |
| `sdiar_prepare_process` | Check the provider process. | Status. |
| `sdiar_create` | Load a model with a default runtime. | Model handle or NULL. |
| `sdiar_create_with_runtime` | Load a model into an empty owner slot. | Status. |
| `sdiar_create_error_code` | Read the last create failure. | Status. |
| `sdiar_create_error` | Describe the last create failure. | Borrowed thread-local string. |
| `sdiar_destroy` | Release a model created by the caller. | None. |
| `sdiar_destroy_owned` | Clear the owner slot and release the model. | Status. |
| `sdiar_get_protection_stats` | Read protected model memory counters. | Status. |
| `sdiar_reset` | Clear request data while retaining the loaded model. | Status. |
| `sdiar_set_progress_callback` | Register or clear a synchronous observer. | Status. |
| `sdiar_feed` | Copy a block of mono 16 kHz float PCM. | Status. |
| `sdiar_finish` | Process the complete recording. | Queued turn count or error. |
| `sdiar_default_request_options` | Set defaults for per-recording count constraints. | Options by value. |
| `sdiar_finish_with_options` | Complete a recording with explicit count constraints. | Queued turn count or error. |
| `sdiar_read_segments` | Copy and consume queued turns. | Copied count or error. |
| `sdiar_get_info` | Query input format and model dimensions. | Status. |
| `sdiar_get_stats` | Query request statistics. | Status. |
| `sdiar_last_error` | Describe the last model error. | Borrowed model string. |
| `pandora_runtime_create` | Create a runtime into an empty owner slot. | Status. |
| `pandora_runtime_get_status` | Read the CPU mask, NPU and QNN settings. | Status. |
| `pandora_runtime_destroy` | Clear the owner slot and close the runtime. | Status. |
| `pandora_runtime_last_error` | Describe the last runtime error. | Borrowed thread-local string. |

## Error codes

| Code | Meaning | What the caller should do |
| --- | --- | --- |
| `0` | Success for status calls. | Continue. |
| `-1` | Invalid argument. | Check input and options. |
| `-2` | Invalid state. | Check call order, owner and process. |
| `-3` | Model or resource error. | Check the package, target and authorization. |
| `-4` | NPU error. | Check device setup and inspect the error text. |
| `-5` | Internal failure. | Release the model and retain the error text. |

## Threads and ownership

Serialize calls on each handle, including queries and destruction. The engine
copies the supplied PCM during each feed and the caller owns segment arrays.
Output times are seconds relative to the recording, with exclusive ends.
Padding the last window can produce segments that extend beyond the input.
For intervals within the recording, clamp the start to at least zero and the
end to at most the input duration (total samples fed divided by 16,000).
Discard any interval whose clipped end is less than or equal to its start.
Standard segments can overlap and exclusive segments cannot. Borrowed error
text is valid until the next call on its owner or thread. The caller owns
callback state through callback replacement or model destruction. Callbacks
run synchronously during finish and must avoid re-entering the model.
The default model entry point applies the SDK's hardware defaults. With an
explicit runtime, the model keeps its own settings snapshot, so it remains usable
after the caller releases the runtime handle. See [Runtime](../sdk/RUNTIME_API.md)
for settings and [the shared header](../sdk/include/pandora_runtime.h) for
runtime-specific error codes.

## Example

A complete program is in [examples/c/runtime.c](../examples/c/runtime.c).
