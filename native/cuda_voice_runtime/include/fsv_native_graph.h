#pragma once

#include <stddef.h>
#include <stdint.h>

#include "fsv_cuda_voice_runtime.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fsv_graph_tensor {
    const char* name;
    const void* data;
    int32_t dtype;
    const int64_t* dims;
    int32_t ndim;
} fsv_graph_tensor;

typedef struct fsv_graph_output {
    const char* name;
    void* data;
    int32_t dtype;
    int64_t* dims;
    int32_t ndim;
} fsv_graph_output;

struct fsv_graph_handle;

FSV_CUDA_API struct fsv_graph_handle* fsv_graph_create(
    const char* model_path, const char* external_weights);
FSV_CUDA_API int fsv_graph_input_count(struct fsv_graph_handle* handle);
FSV_CUDA_API const char* fsv_graph_input_name(struct fsv_graph_handle* handle, int index);
FSV_CUDA_API int fsv_graph_output_count(struct fsv_graph_handle* handle);
FSV_CUDA_API const char* fsv_graph_output_name(struct fsv_graph_handle* handle, int index);
FSV_CUDA_API int fsv_graph_run(
    struct fsv_graph_handle* handle,
    const fsv_graph_tensor* inputs, size_t input_count,
    fsv_graph_output* outputs, size_t output_count);
FSV_CUDA_API void fsv_graph_free(struct fsv_graph_handle* handle);
FSV_CUDA_API void fsv_graph_release_outputs(fsv_graph_output* outputs, size_t count);
FSV_CUDA_API int fsv_graph_get_tensor(
    struct fsv_graph_handle* handle, const char* name, fsv_graph_output* output);
/* Names of the intermediate tensors kept by the last run. Only populated when
   FSV_NATIVE_CAPTURE is set; used by the development comparison tools. */
FSV_CUDA_API int fsv_graph_captured_count(struct fsv_graph_handle* handle);
FSV_CUDA_API const char* fsv_graph_captured_name(struct fsv_graph_handle* handle, int index);
FSV_CUDA_API void fsv_graph_set_capture(struct fsv_graph_handle* handle, int enabled);
/* Declares the {output, input} pairs whose value must stay on the device
   between runs: ``names`` holds 2 * pair_count entries, output first. A run
   then reports such an output with a null ``data``, meaning "keep passing this
   value back", and an input the caller does not supply is read from the
   retained buffer. Autoregressive KV caches are what this exists for. */
FSV_CUDA_API void fsv_graph_set_resident(
    struct fsv_graph_handle* handle, const char* const* names, int pair_count);
/* Host bytes of a retained output, for a caller that really wants to read it. */
FSV_CUDA_API int fsv_graph_read_resident(
    struct fsv_graph_handle* handle, const char* name, fsv_graph_output* output);
FSV_CUDA_API const char* fsv_graph_last_error(void);

#ifdef __cplusplus
}
#endif
