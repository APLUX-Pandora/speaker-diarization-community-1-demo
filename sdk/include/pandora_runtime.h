/* SPDX-License-Identifier: Apache-2.0
 * Pandora Runtime C ABI 5: hardware settings shared by every Pandora model SDK on Qualcomm
 * platforms (CPU affinity, QNN performance mode, QNN priority, NPU device).
 * Shared Pandora Runtime header; ship it unchanged.
 *
 * Most applications never create a Runtime: a model loaded with a NULL runtime uses the
 * defaults below. Settings are fixed for the lifetime of a Runtime.
 *
 *   Default: CPU affinity "default" (by SoC), performance mode burst, priority normal, device 0.
 *   Default CPUs: soc_id 702 (QCS8625) -> CPU 2,3,7 (0x8c); soc_id 675 (QCS8275) -> CPU 1,2,3
 *   (0x0e); soc_id 603 (QCS8550) with 6 present CPUs -> CPU 3,4,5 (0x38), with 8 -> 0xe0; any
 *   other SoC, or no soc_id -> CPU 5,6,7 (0xe0). The CPUs must exist, be online and
 *   be allowed for the process; otherwise creation fails. The mask is never narrowed.
 *   RPC is set by the SDK from the performance mode: control latency 100 us; polling 9999 us for
 *   burst, sustained_high_performance and high_performance, 0 for the other modes.
 *
 * Handles belong to one Runtime provider and process. Migrated models share that provider.
 * Never pass a handle to a legacy provider or use it after fork. A model keeps its own reference, so a Runtime
 * may be destroyed before the models that use it; shared state is released with the last model.
 */
#ifndef PANDORA_RUNTIME_H
#define PANDORA_RUNTIME_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
#ifndef PANDORA_RUNTIME_API
#define PANDORA_RUNTIME_API __attribute__((visibility("default")))
#endif

#define PANDORA_RUNTIME_ABI_VERSION 5
#define PANDORA_RUNTIME_MAX_CPUS 64

enum {
    PANDORA_RUNTIME_OK = 0,
    PANDORA_RUNTIME_ERROR_ARGUMENT = -1,    /* invalid type, value, size or ABI version */
    PANDORA_RUNTIME_ERROR_UNSUPPORTED = -2, /* valid value this SDK or target cannot apply */
    PANDORA_RUNTIME_ERROR_STATE = -3,       /* wrong process (fork), closed or busy */
    PANDORA_RUNTIME_ERROR_BACKEND = -4,     /* QNN/Genie rejected a setting */
    PANDORA_RUNTIME_ERROR_INTERNAL = -5
};

enum { PANDORA_CPU_AFFINITY_DEFAULT = 0, PANDORA_CPU_AFFINITY_LIST = 1 };

/* Highest performance to lowest power. */
enum {
    PANDORA_QNN_BURST = 0,
    PANDORA_QNN_SUSTAINED_HIGH_PERFORMANCE = 1,
    PANDORA_QNN_HIGH_PERFORMANCE = 2,
    PANDORA_QNN_BALANCED = 3,
    PANDORA_QNN_LOW_BALANCED = 4,
    PANDORA_QNN_HIGH_POWER_SAVER = 5,
    PANDORA_QNN_POWER_SAVER = 6,
    PANDORA_QNN_LOW_POWER_SAVER = 7,
    PANDORA_QNN_EXTREME_POWER_SAVER = 8,
    PANDORA_QNN_SYSTEM_SETTINGS = 9
};

/* Same values as Qnn_Priority_t and GenieDialog_Priority_t. */
enum {
    PANDORA_PRIORITY_LOW = 0,
    PANDORA_PRIORITY_NORMAL = 100,
    PANDORA_PRIORITY_NORMAL_HIGH = 150,
    PANDORA_PRIORITY_HIGH = 200
};

typedef struct pandora_runtime pandora_runtime_t;

typedef struct {
    uint32_t struct_size;      /* sizeof(pandora_runtime_config_t) */
    uint32_t abi_version;      /* PANDORA_RUNTIME_ABI_VERSION */
    uint32_t cpu_affinity;     /* PANDORA_CPU_AFFINITY_* */
    uint32_t cpu_count;        /* LIST: 1..PANDORA_RUNTIME_MAX_CPUS; DEFAULT: 0 */
    const uint32_t *cpu_ids;   /* LIST: CPU IDs 0..63, duplicates rejected; DEFAULT: NULL */
    uint32_t performance_mode; /* PANDORA_QNN_* */
    uint32_t priority;         /* PANDORA_PRIORITY_* */
    uint32_t device_id;        /* NPU device; only QCS9075 has device 1 */
    uint32_t reserved;         /* 0 */
} pandora_runtime_config_t;

#define PANDORA_RUNTIME_CONFIG_INIT \
    { sizeof(pandora_runtime_config_t), PANDORA_RUNTIME_ABI_VERSION, PANDORA_CPU_AFFINITY_DEFAULT, \
      0, NULL, PANDORA_QNN_BURST, PANDORA_PRIORITY_NORMAL, 0, 0 }

/* Read-only snapshot of the settings submitted to the backend. */
typedef struct {
    uint32_t struct_size;            /* caller sets sizeof(pandora_runtime_status_t) */
    uint32_t abi_version;            /* caller sets PANDORA_RUNTIME_ABI_VERSION */
    int32_t soc_id;                  /* /sys/devices/soc0/soc_id, or -1 when unreadable */
    uint32_t cpu_affinity;           /* requested PANDORA_CPU_AFFINITY_* */
    uint32_t cpu_count;              /* CPUs in cpu_mask */
    uint32_t cpu_ids[PANDORA_RUNTIME_MAX_CPUS]; /* ascending; first cpu_count are valid */
    uint64_t cpu_mask;               /* submitted mask, e.g. 0xe0 */
    uint32_t performance_mode;
    uint32_t priority;
    uint32_t device_id;
    uint32_t rpc_control_latency_us;
    uint32_t rpc_polling_time_us;
    uint32_t reserved;
} pandora_runtime_status_t;

/* NULL config means PANDORA_RUNTIME_CONFIG_INIT. *out must be NULL on entry. */
PANDORA_RUNTIME_API int pandora_runtime_create(const pandora_runtime_config_t *config,
                                               pandora_runtime_t **out);
PANDORA_RUNTIME_API int pandora_runtime_get_status(const pandora_runtime_t *runtime,
                                                   pandora_runtime_status_t *out);
/* Releases the caller's reference and sets *runtime to NULL. NULL or *runtime == NULL is OK,
 * so calling it twice is safe. Models created with this Runtime stay valid. */
PANDORA_RUNTIME_API int pandora_runtime_destroy(pandora_runtime_t **runtime);
/* Message of the last failed call on this thread; empty after a successful call. */
PANDORA_RUNTIME_API const char *pandora_runtime_last_error(void);

#ifdef __cplusplus
}
#endif
#endif
