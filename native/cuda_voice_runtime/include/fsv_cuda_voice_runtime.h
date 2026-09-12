#pragma once

#include <stddef.h>

#if defined(_WIN32)
#  if defined(FSV_CUDA_VOICE_BUILD_DLL)
#    define FSV_CUDA_API __declspec(dllexport)
#  else
#    define FSV_CUDA_API __declspec(dllimport)
#  endif
#else
#  define FSV_CUDA_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fsv_cuda_device_info {
    int index;
    int major;
    int minor;
    size_t global_memory_bytes;
    char name[256];
} fsv_cuda_device_info;

FSV_CUDA_API int fsv_cuda_device_count(void);
FSV_CUDA_API int fsv_cuda_get_device_info(int index, fsv_cuda_device_info* out);
/* Index of the card device selection picked for this process, or -1 when there
   is none (``fsv_cuda_last_error`` then says why). ``name`` receives
   "GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)" when it is not null. Only cards
   with at least 3 GiB are picked automatically, because the acoustic graphs
   peak around 2.7 GiB; AEMEATH_CUDA_VOICE_DEVICE forces an index instead, and
   its value "none" (or "-1") turns the device path off completely. */
FSV_CUDA_API int fsv_cuda_active_device(char* name, size_t name_size);
FSV_CUDA_API const char* fsv_cuda_last_error(void);

/* Non-zero when a device allocation has been refused since the previous call.
   Reading it clears the flag, so the graph interpreter can report an
   out-of-memory degradation once instead of once per declined node. */
FSV_CUDA_API int fsv_cuda_take_alloc_failure(void);

/* Non-zero while the current run has given up on the card: after a run of
   refusals every remaining node executes on the host, so the graph finishes at
   host speed instead of stalling on a full card. The graph runtime resets this
   at the start of each run, and callers can also query it to report the
   degradation. */
FSV_CUDA_API int fsv_cuda_device_abandoned(void);
FSV_CUDA_API void fsv_cuda_reset_device_abandoned(void);

FSV_CUDA_API int fsv_cuda_binary_f32(const float* a, const float* b, float* c, size_t count, int operation);
FSV_CUDA_API int fsv_cuda_unary_f32(const float* a, float* c, size_t count, int operation);
FSV_CUDA_API int fsv_cuda_binary_f32_host(const float* a, const float* b, float* c, size_t count, int operation);
FSV_CUDA_API int fsv_cuda_unary_f32_host(const float* a, float* c, size_t count, int operation);

/* Normalize each contiguous row over its last dimension. */
FSV_CUDA_API int fsv_cuda_layernorm_f32(
    const float* input, const float* scale, const float* bias,
    float* output, int rows, int width, float epsilon);

/* Softmax over the last contiguous dimension. */
FSV_CUDA_API int fsv_cuda_softmax_f32(
    const float* input, float* output, int rows, int width);
FSV_CUDA_API int fsv_cuda_layernorm_f32_host(
    const float* input, const float* scale, const float* bias, float* output,
    int rows, int width, float epsilon);
FSV_CUDA_API int fsv_cuda_softmax_f32_host(
    const float* input, float* output, int rows, int width);

/* Row-major C[M,K] x B[K,N] -> C[M,N]. */
FSV_CUDA_API int fsv_cuda_matmul_f32(
    const float* a, const float* b, float* c, int m, int k, int n);
FSV_CUDA_API int fsv_cuda_matmul_f32_host(
    const float* a, const float* b, float* c, int m, int k, int n);
FSV_CUDA_API int fsv_cuda_conv2d_f32_host(
    const float* input, const float* weight, const float* bias, float* output,
    int batch, int channels, int height, int width, int out_channels,
    int kernel_h, int kernel_w, int pad_top, int pad_left, int pad_bottom, int pad_right,
    int stride_h, int stride_w, int dilation_h, int dilation_w, int groups);
/* 1-D ConvTranspose, ONNX weight layout [C_in, C_out/groups, K]. */
FSV_CUDA_API int fsv_cuda_conv_transpose1d_f32_host(
    const float* input, const float* weight, const float* bias, float* output,
    int batch, int channels, int width, int out_channels, int kernel,
    int pad_left, int pad_right, int stride, int dilation, int groups,
    int output_padding);

/* INT8 A/B are dequantized by per-tensor scales before accumulation. */
FSV_CUDA_API int fsv_cuda_matmul_i8(
    const signed char* a, const signed char* b, float* c,
    int m, int k, int n, float a_scale, float b_scale);

/* Packed signed INT4, two values per byte, zero point 8. */
FSV_CUDA_API int fsv_cuda_matmul_i4(
    const signed char* a, const unsigned char* packed_b, float* c,
    int m, int k, int n, float a_scale, float b_scale);

/* ORT MatMulNBits layout: packed B is [N, ceil(K/block_size), block_size/2],
   scales is [N, ceil(K/block_size)], symmetric signed INT4 (nibble - 8). */
FSV_CUDA_API int fsv_cuda_matmul_nbits_f32(
    const float* a, const unsigned char* packed_b, const float* scales,
    float* c, int m, int k, int n, int block_size, int bits);

FSV_CUDA_API int fsv_cuda_matmul_nbits_f16scales(
    const float* a, const unsigned char* packed_b, const unsigned short* scales,
    float* c, int m, int k, int n, int block_size, int bits);

/* Host-memory convenience entry points for integration probes and small tests. */
FSV_CUDA_API int fsv_cuda_matmul_nbits_f32_host(
    const float* a, const unsigned char* packed_b, const float* scales,
    float* c, int m, int k, int n, int block_size, int bits);
FSV_CUDA_API int fsv_cuda_matmul_nbits_f16scales_host(
    const float* a, const unsigned char* packed_b, const unsigned short* scales,
    float* c, int m, int k, int n, int block_size, int bits);

/* Constant buffers (graph weights) stay resident on the device: the host
   pointer is remembered until it is unregistered, so the hundreds of nodes
   that read the same weight pay for a single upload. */
FSV_CUDA_API int fsv_cuda_constant_register(const void* host, size_t bytes);
FSV_CUDA_API void fsv_cuda_constant_unregister(const void* host);
FSV_CUDA_API void fsv_cuda_constant_clear(void);


/* -------------------------------------------------------------------------
   Device-resident tensors.

   Callers that keep whole tensors in device memory (the graph runtime does)
   use these entry points instead of the host-memory helpers above. A pointer
   comes from fsv_cuda_device_alloc or fsv_cuda_constant_lookup and stays
   valid until it is freed. Calls are ordered on the driver's default stream
   and never block on the device; only fsv_cuda_device_download waits for the
   work queued before it, which is what makes a chain of operators run without
   a round trip through system memory.
   ------------------------------------------------------------------------- */

typedef unsigned long long fsv_cuda_ptr;

FSV_CUDA_API int fsv_cuda_device_alloc(size_t bytes, fsv_cuda_ptr* pointer);
FSV_CUDA_API void fsv_cuda_device_free(fsv_cuda_ptr pointer, size_t bytes);
FSV_CUDA_API int fsv_cuda_device_upload(fsv_cuda_ptr pointer, const void* host, size_t bytes);
FSV_CUDA_API int fsv_cuda_device_download(void* host, fsv_cuda_ptr pointer, size_t bytes);
/* Waits for every queued kernel and reports the first asynchronous failure. */
FSV_CUDA_API int fsv_cuda_device_sync(void);
/* Returns 0 and the resident pointer when the host buffer is already cached
   on the device (graph weights), non-zero when the caller must upload. */
FSV_CUDA_API int fsv_cuda_constant_lookup(const void* host, size_t bytes,
                                          fsv_cuda_ptr* pointer);

/* Index tables for the broadcast and gather kernels. The layout mirrors
   FsvIndex in src/kernels/fsv_kernels.cu; rank 0 means both operands are
   contiguous with the output shape. a_stride/b_stride are element strides in
   output dimension order, a zero stride broadcasting that dimension. */
typedef struct fsv_cuda_index {
    long long shape[8];
    long long a_stride[8];
    long long b_stride[8];
    int rank;
    int operation;
} fsv_cuda_index;

FSV_CUDA_API int fsv_cuda_binary_bcast_f32(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c,
                                           size_t count, const fsv_cuda_index* index);
FSV_CUDA_API int fsv_cuda_compare_bcast_u8(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c,
                                           size_t count, const fsv_cuda_index* index);
FSV_CUDA_API int fsv_cuda_unary_ops_f32(fsv_cuda_ptr a, fsv_cuda_ptr c, size_t count,
                                        int operation, float alpha);
FSV_CUDA_API int fsv_cuda_clamp_f32(fsv_cuda_ptr a, fsv_cuda_ptr c, size_t count,
                                    int has_low, float low, int has_high, float high);
/* out[n][c][s] = in[n][c][s] * scale[c] + shift[c] */
FSV_CUDA_API int fsv_cuda_channel_affine_f32(fsv_cuda_ptr input, fsv_cuda_ptr scale,
                                             fsv_cuda_ptr shift, fsv_cuda_ptr output,
                                             long long batch, long long channels,
                                             long long spatial);
FSV_CUDA_API int fsv_cuda_transpose_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                        size_t count, int rank, const fsv_cuda_index* index);
/* Transpose fast paths: a batched [batch][rows][cols] -> [batch][cols][rows]
   block staged through shared memory, and the exchange of two axes that each
   carry a contiguous run of `inner` values. Both are plain layout moves, so
   the pointers must be distinct buffers of the same element type. */
FSV_CUDA_API int fsv_cuda_transpose_tile_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                             long long batch, long long rows, long long cols);
FSV_CUDA_API int fsv_cuda_transpose_swap_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                             long long a_size, long long b_size,
                                             long long inner);
FSV_CUDA_API int fsv_cuda_copy_nd_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                      size_t count, long long source_base,
                                      long long destination_base, int rank,
                                      const fsv_cuda_index* index);
FSV_CUDA_API int fsv_cuda_fill_f32(fsv_cuda_ptr destination, size_t count, float value);
/* Reduce a run of consecutive dimensions: [outer][mid][inner] -> [outer][inner].
   operation uses the same codes as the host reduce: 0 sums, 1 averages, 2 takes
   the maximum and 3 the L2 norm. The caller only routes a node here when its
   axes name such a run, so no coordinate walk is needed. */
FSV_CUDA_API int fsv_cuda_reduce_block_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                           long long outer, long long mid, long long inner,
                                           int operation);
/* Index of the first maximum along the middle axis of a [outer][mid][inner]
   view, written as int64. ArgMax is the token decision of the decode loop, so
   leaving it on the host made every step drain the queue for one scalar. */
FSV_CUDA_API int fsv_cuda_argmax_i64(fsv_cuda_ptr input, fsv_cuda_ptr output,
                                     long long outer, long long mid, long long inner);
/* InstanceNormalization over the trailing dimensions of a
   [rows][spatial] view, with the per-channel scale and bias applied
   afterwards. ``rows`` is batch * channels, so the channel of a row is
   ``row % channels``. */
FSV_CUDA_API int fsv_cuda_instance_norm_f32(fsv_cuda_ptr input, fsv_cuda_ptr scale,
                                            fsv_cuda_ptr bias, fsv_cuda_ptr output,
                                            long long rows, long long channels,
                                            long long spatial, float epsilon);
/* destination = source * factor over a contiguous run. The factor rides as a
   kernel argument instead of a one-element tensor that has to be uploaded and
   broadcast on every Gemm. */
FSV_CUDA_API int fsv_cuda_scale_into_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                         size_t count, float factor);
FSV_CUDA_API int fsv_cuda_gather_f32(fsv_cuda_ptr data, fsv_cuda_ptr indices,
                                     fsv_cuda_ptr output, long long outer,
                                     long long index_count, long long inner,
                                     long long axis_size);
/* GatherElements: out[s] = data[slot with axis replaced by indices[s]]. index
   carries the index tensor's shape with the data tensor's strides in output
   dimension order. */
FSV_CUDA_API int fsv_cuda_gather_elements_f32(fsv_cuda_ptr data, fsv_cuda_ptr indices,
                                              fsv_cuda_ptr output, size_t count,
                                              long long axis, long long axis_size,
                                              const fsv_cuda_index* index);
/* ScatterElements(reduction="none"): the caller copies the operand into
   ``output`` first and this writes the updates over it. ``index`` carries the
   index tensor's shape with the operand's strides in output dimension order,
   the same layout the gather entry point takes. */
FSV_CUDA_API int fsv_cuda_scatter_elements_f32(fsv_cuda_ptr updates, fsv_cuda_ptr indices,
                                               fsv_cuda_ptr output, size_t count,
                                               long long axis, long long axis_size,
                                               const fsv_cuda_index* index);
/* Where(condition, a, b): a byte condition picks between two float operands.
   The condition carries its own stride row because the attention masks
   broadcast it over one of the operands. */
typedef struct fsv_cuda_where_index {
    long long shape[8];
    long long condition_stride[8];
    long long a_stride[8];
    long long b_stride[8];
    int rank;
    int reserved;
} fsv_cuda_where_index;

FSV_CUDA_API int fsv_cuda_where_f32(fsv_cuda_ptr condition, fsv_cuda_ptr a, fsv_cuda_ptr b,
                                    fsv_cuda_ptr out, size_t count,
                                    const fsv_cuda_where_index* index);

/* Device-pointer variants of the operators that also have host entry points. */
FSV_CUDA_API int fsv_cuda_matmul_dev(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c,
                                     int m, int k, int n);
/* Batched matrix product: c[slice, m, n] = a[...] @ b[...] over a broadcast
   batch shape. index is the batch shape with strides in output batch order,
   already scaled to whole m*k and k*n matrices; rank 0 means both operands are
   plain matrices. The graph runtime used to issue one launch per slice. */
FSV_CUDA_API int fsv_cuda_matmul_f32_batched(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c,
                                             int m, int k, int n, long long batch,
                                             long long slice_offset,
                                             const fsv_cuda_index* index);
FSV_CUDA_API int fsv_cuda_conv2d_dev(
    fsv_cuda_ptr input, fsv_cuda_ptr weight, fsv_cuda_ptr bias, fsv_cuda_ptr output,
    int batch, int channels, int height, int width, int out_channels,
    int kernel_h, int kernel_w, int pad_top, int pad_left, int pad_bottom, int pad_right,
    int stride_h, int stride_w, int dilation_h, int dilation_w, int groups);
FSV_CUDA_API int fsv_cuda_conv_transpose1d_dev(
    fsv_cuda_ptr input, fsv_cuda_ptr weight, fsv_cuda_ptr bias, fsv_cuda_ptr output,
    int batch, int channels, int width, int out_channels, int kernel,
    int pad_left, int pad_right, int stride, int dilation, int groups, int output_padding);
FSV_CUDA_API int fsv_cuda_matmul_nbits_dev(
    fsv_cuda_ptr a, fsv_cuda_ptr packed_b, fsv_cuda_ptr scales, fsv_cuda_ptr c,
    int m, int k, int n, int block_size, int bits, int scales_are_f16);
FSV_CUDA_API int fsv_cuda_layernorm_dev(fsv_cuda_ptr input, fsv_cuda_ptr scale,
                                        fsv_cuda_ptr bias, fsv_cuda_ptr output,
                                        int rows, int width, float epsilon);
FSV_CUDA_API int fsv_cuda_softmax_dev(fsv_cuda_ptr input, fsv_cuda_ptr output,
                                      int rows, int width);

#ifdef __cplusplus
}
#endif
