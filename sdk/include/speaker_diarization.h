#ifndef SPEAKER_DIARIZATION_H
#define SPEAKER_DIARIZATION_H

#include <stddef.h>
#include <stdint.h>
#include "pandora_runtime.h"

#if defined(_WIN32)
#  if defined(SDIAR_BUILDING_LIBRARY)
#    define SDIAR_API __declspec(dllexport)
#  else
#    define SDIAR_API __declspec(dllimport)
#  endif
#elif defined(__GNUC__)
#  define SDIAR_API __attribute__((visibility("default")))
#else
#  define SDIAR_API
#endif

#define SDIAR_VERSION "0.6.0"
#define SDIAR_ABI_VERSION 1u

#ifdef __cplusplus
extern "C" {
#endif

typedef struct sdiar_engine sdiar_engine;

typedef enum sdiar_status {
    SDIAR_OK = 0,
    SDIAR_ERROR_ARGUMENT = -1,
    SDIAR_ERROR_STATE = -2,
    SDIAR_ERROR_MODEL = -3,
    SDIAR_ERROR_NPU = -4,
    SDIAR_ERROR_INTERNAL = -5
} sdiar_status;

typedef enum sdiar_state {
    SDIAR_STATE_COLLECTING = 0,
    SDIAR_STATE_FINISHED = 1,
    SDIAR_STATE_FAILED = 2
} sdiar_state;

typedef enum sdiar_segment_kind {
    SDIAR_SEGMENT_STANDARD = 0,
    SDIAR_SEGMENT_EXCLUSIVE = 1
} sdiar_segment_kind;

typedef enum sdiar_progress_stage {
    SDIAR_PROGRESS_SEGMENTATION = 1,
    SDIAR_PROGRESS_EMBEDDING = 2,
    SDIAR_PROGRESS_CLUSTERING = 3,
    SDIAR_PROGRESS_RECONSTRUCTION = 4,
    SDIAR_PROGRESS_COMPLETE = 5
} sdiar_progress_stage;

typedef void (*sdiar_progress_callback)(void *user_data, uint32_t stage,
                                      uint64_t completed, uint64_t total);

typedef struct sdiar_options {
    uint32_t struct_size;
    uint32_t abi_version;
    int32_t cpu_threads;
    uint32_t reserved_flags;
    /* NULL uses the bundled QNN runtime. Any explicit path must name its host directory. */
    const char *qnn_library_dir;
    uint64_t reserved[8];
} sdiar_options;

/* Per-recording clustering constraints. Zero means unspecified. num_speakers
 * overrides both bounds. With no exact count, min must not exceed a nonzero max.
 * Values must fit int32. Constraints do not create speech: silence stays empty;
 * fewer than two clean training embeddings retain the reference single-cluster
 * fallback. Otherwise requesting more clusters than training rows is an error.
 * The final observed speaker count can be lower when embeddings coincide or
 * reconstructed turns contain no speech for a cluster. No identities are made up.
 */
typedef struct sdiar_request_options {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t num_speakers;
    uint32_t min_speakers;
    uint32_t max_speakers;
    uint32_t reserved_flags;
    uint64_t reserved[4];
} sdiar_request_options;

typedef struct sdiar_info {
    uint32_t struct_size;
    uint32_t abi_version;
    uint32_t sample_rate;
    uint32_t audio_channels;
    double window_seconds;
    double step_seconds;
    uint32_t segmentation_frames;
    uint32_t local_speakers;
    uint32_t powerset_classes;
    uint32_t embedding_dimension;
    /* Final speaker IDs and segments become available only after finish. */
    uint32_t final_results_only;
    uint32_t reserved_flags;
    uint64_t reserved[8];
} sdiar_info;

typedef struct sdiar_segment {
    /* Seconds from the first input sample; interval is [start, end). */
    double start_seconds;
    double end_seconds;
    /* The same speaker ID has the same meaning in both segment kinds. */
    uint32_t speaker_id;
    uint32_t kind;
} sdiar_segment;

typedef struct sdiar_stats {
    uint32_t struct_size;
    uint32_t state;
    uint64_t input_samples;
    uint64_t segmentation_invocations;
    uint64_t embedding_invocations;
    uint64_t pending_segments;
    uint32_t detected_speakers;
    uint32_t reserved_flags;
    double model_load_ms;
    double segmentation_execute_ms;
    double embedding_execute_ms;
    double fbank_cpu_ms;
    double mask_cpu_ms;
    double clustering_cpu_ms;
    double reconstruction_cpu_ms;
    double graph_io_ms;
    double processing_wall_ms;
    uint64_t reserved[8];
} sdiar_stats;

/*
 * Synchronous engine; no background inference or audio/network IO.
 * Runtime may own a power-housekeeping thread. cpu_threads is retained for ABI
 * compatibility, without configuring vendor pools or single-threaded algorithms.
 * Model and runtime assets are loaded during create.
 * Serialize all calls on one handle, including reads and destruction.
 * Separate handles may be used independently; SDK code has no shared session
 * state. Concurrent NPU scheduling is subject to the vendor runtime.
 * No C++ exception crosses this ABI. Reserved fields must be zero.
 */
SDIAR_API const char *sdiar_version(void);
SDIAR_API sdiar_options sdiar_default_options(void);

/* Compatibility no-op. Does not change dumpability, limits or signal policy. */
SDIAR_API int32_t sdiar_prepare_process(void);

/*
 * Loads models.json, CPU parameters and both QNN contexts once.
 * options may be NULL for defaults. model_dir is a UTF-8 filesystem path.
 * Initial state is COLLECTING. Returns NULL on failure; partial resources
 * are released. Report details using the thread-local create error functions.
 */
SDIAR_API sdiar_engine *sdiar_create(const char *model_dir, const sdiar_options *options);
SDIAR_API int32_t sdiar_create_error_code(void);
SDIAR_API const char *sdiar_create_error(void);
SDIAR_API void sdiar_destroy(sdiar_engine *engine);
/* Additive owner-slot API. *out must be NULL. Runtime is provider/process-local;
 * NULL applies the shared defaults. A loaded model keeps its runtime settings.
 * destroy_owned clears the slot before teardown and may be retried with NULL.
 * A teardown error is returned if the backend could not be safely released.
 * Serialize creation/query/close; finish is synchronous, not cancellable. */
SDIAR_API int32_t sdiar_create_with_runtime(const char *model_dir, const sdiar_options *options,
                                         pandora_runtime_t *runtime, sdiar_engine **out);
SDIAR_API int32_t sdiar_destroy_owned(sdiar_engine **owner);

/* Provider-local counters; snapshots are observational, not synchronization.
 * Bytes describe owned encrypted/decrypted mappings, not opaque vendor copies.
 * The legacy full-graph lock counters are zero. */
typedef struct sdiar_protection_stats {
 uint32_t struct_size, abi_version;
 uint64_t live_mappings, mapped_bytes, dump_excluded_bytes, locked_bytes;
 uint64_t lock_failures, zeroed_bytes;
} sdiar_protection_stats;
SDIAR_API int32_t sdiar_get_protection_stats(sdiar_protection_stats *out);

/*
 * Clears PCM, outputs, clustering state and session counters, retaining loaded
 * models and model_load_ms. Allowed in any valid handle state. Returns 0.
 * reset is not an asynchronous cancellation primitive.
 */
SDIAR_API int32_t sdiar_reset(sdiar_engine *engine);

/* Optional observer invoked synchronously inside finish, on its calling thread.
 * Set only while COLLECTING. NULL disables it. Registration survives reset.
 * The callback must not throw or call any API on this handle. Keep user_data
 * and the callback alive until replaced or the engine is destroyed.
 * Progress reports completed work; it provides no partial speaker labels.
 */
SDIAR_API int32_t sdiar_set_progress_callback(sdiar_engine *engine,
                                            sdiar_progress_callback callback, void *user_data);

/*
 * Appends mono 16 kHz PCM float samples. Values must be finite; the SDK does
 * not clip or normalize. sample_count is a sample count, not a byte count.
 * Zero samples with pcm == NULL is allowed. Input memory is copied before
 * return. Valid only in COLLECTING. Returns 0 (no final segments yet).
 */
SDIAR_API int32_t sdiar_feed(sdiar_engine *engine, const float *pcm, size_t sample_count);

/*
 * Runs the complete offline model and queues both output kinds. Finalization
 * and padding follow the reference; zero input yields an empty result.
 * Valid only in COLLECTING. Returns the number of queued segments, or a
 * negative error. Failure enters FAILED; reset is required before more input.
 * Given identical PCM, feed chunk boundaries do not affect final outputs.
 */
SDIAR_API int32_t sdiar_finish(sdiar_engine *engine);

/* Additive request API; existing sdiar_options, ABI and sdiar_finish are retained.
 * NULL selects automatic counting. Options are copied for this finish only and
 * do not survive reset or affect the next recording. Structural validation errors
 * leave the collecting session unchanged; processing errors enter FAILED.
 */
SDIAR_API sdiar_request_options sdiar_default_request_options(void);
SDIAR_API int32_t sdiar_finish_with_options(sdiar_engine *engine,
                                          const sdiar_request_options *options);

/*
 * Drains at most capacity segments. Sorted by (kind, start, end, speaker_id).
 * STANDARD segments may overlap. EXCLUSIVE segments do not overlap.
 * Final-window padding can produce segments that reach beyond the input
 * duration (total samples fed / 16000.0). For intervals within the recording,
 * clamp the start to at least zero and the end to at most the input duration.
 * Discard intervals whose clipped end is less than or equal to their start.
 * IDs are session-local and deterministic; they are not identities across
 * recordings. Returns items copied, 0 if empty, or a negative error.
 * capacity == 0 with output == NULL is allowed and consumes nothing.
 * Valid in COLLECTING or FINISHED; FAILED returns SDIAR_ERROR_STATE.
 */
SDIAR_API int32_t sdiar_read_segments(sdiar_engine *engine, sdiar_segment *output, uint32_t capacity);

/* Set output->struct_size = sizeof(*output) before either query. */
SDIAR_API int32_t sdiar_get_info(sdiar_engine *engine, sdiar_info *output);
SDIAR_API int32_t sdiar_get_stats(sdiar_engine *engine, sdiar_stats *output);

/*
 * Error text is UTF-8, owned by the SDK, valid until the next API call on
 * the handle. The create error is thread-local, valid until the next create
 * or Runtime/protection/prepare call on that thread. Consume immediately; do not free.
 */
SDIAR_API const char *sdiar_last_error(const sdiar_engine *engine);

#ifdef __cplusplus
}
#endif
#endif
