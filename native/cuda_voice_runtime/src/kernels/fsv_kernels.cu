/* Device-only kernels for the self-written voice runtime.
   This file is compiled to PTX at build time and embedded in the DLL; the
   driver JIT-compiles it on the user machine, so no CUDA toolkit, cudart or
   cuBLAS is needed at run time. */

#include <cuda_fp16.h>
#include <math_constants.h>

#define FSV_KERNEL extern "C" __global__

namespace {

__device__ __forceinline__ signed char unpack_i4(unsigned char value, bool high) {
    int nibble = high ? (value >> 4) : (value & 0x0f);
    return static_cast<signed char>(nibble - 8);
}

}  // namespace

FSV_KERNEL void fsv_binary_f32(const float* a, const float* b, float* c,
                               unsigned long long count, int operation) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    if (operation == 0) c[index] = a[index] + b[index];
    else if (operation == 1) c[index] = a[index] * b[index];
    else c[index] = a[index] - b[index];
}

FSV_KERNEL void fsv_unary_f32(const float* a, float* c, unsigned long long count, int operation) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    float value = a[index];
    if (operation == 0) c[index] = fmaxf(value, 0.0f);
    else if (operation == 1) c[index] = tanhf(value);
    else if (operation == 2) c[index] = 1.0f / (1.0f + expf(-value));
    else if (operation == 3) c[index] = expf(value);
    else if (operation == 4) c[index] = sqrtf(value);
    else if (operation == 5) c[index] = logf(value);
    else if (operation == 6) c[index] = erff(value);
    else c[index] = value;
}

FSV_KERNEL void fsv_layernorm_f32(const float* input, const float* scale, const float* bias,
                                  float* output, int rows, int width, float epsilon) {
    /* One warp per row. Reducing a row on a single thread left 31 of every 32
       lanes idle and turned a [1025, 512] normalization into a serial chain of
       1536 dependent steps; the shuffles below keep the same three passes but
       spread each of them over the whole warp. */
    const int lane = threadIdx.x & 31;
    const int row = blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5);
    if (row >= rows || width <= 0) return;
    const float* row_input = input + static_cast<size_t>(row) * width;
    float* row_output = output + static_cast<size_t>(row) * width;
    float sum = 0.0f;
    for (int i = lane; i < width; i += 32) sum += row_input[i];
    for (int offset = 16; offset > 0; offset >>= 1) {
        sum += __shfl_down_sync(0xffffffffu, sum, offset);
    }
    sum = __shfl_sync(0xffffffffu, sum, 0);
    float mean = sum / static_cast<float>(width);
    float variance = 0.0f;
    for (int i = lane; i < width; i += 32) {
        float delta = row_input[i] - mean;
        variance += delta * delta;
    }
    for (int offset = 16; offset > 0; offset >>= 1) {
        variance += __shfl_down_sync(0xffffffffu, variance, offset);
    }
    variance = __shfl_sync(0xffffffffu, variance, 0);
    float inv_std = rsqrtf(variance / static_cast<float>(width) + epsilon);
    for (int i = lane; i < width; i += 32) {
        float normalized = (row_input[i] - mean) * inv_std;
        row_output[i] = normalized * (scale ? scale[i] : 1.0f) + (bias ? bias[i] : 0.0f);
    }
}

FSV_KERNEL void fsv_softmax_f32(const float* input, float* output, int rows, int width) {
    const int lane = threadIdx.x & 31;
    const int row = blockIdx.x * (blockDim.x >> 5) + (threadIdx.x >> 5);
    if (row >= rows || width <= 0) return;
    const float* row_input = input + static_cast<size_t>(row) * width;
    float* row_output = output + static_cast<size_t>(row) * width;
    float maximum = -CUDART_INF_F;
    for (int i = lane; i < width; i += 32) maximum = fmaxf(maximum, row_input[i]);
    for (int offset = 16; offset > 0; offset >>= 1) {
        maximum = fmaxf(maximum, __shfl_down_sync(0xffffffffu, maximum, offset));
    }
    maximum = __shfl_sync(0xffffffffu, maximum, 0);
    float total = 0.0f;
    for (int i = lane; i < width; i += 32) total += expf(row_input[i] - maximum);
    for (int offset = 16; offset > 0; offset >>= 1) {
        total += __shfl_down_sync(0xffffffffu, total, offset);
    }
    total = __shfl_sync(0xffffffffu, total, 0);
    for (int i = lane; i < width; i += 32) {
        row_output[i] = expf(row_input[i] - maximum) / total;
    }
}

FSV_KERNEL void fsv_matmul_f32(const float* a, const float* b, float* c,
                               int m, int k, int n, float scale) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= m || col >= n) return;
    float sum = 0.0f;
    for (int index = 0; index < k; ++index) {
        sum += a[static_cast<size_t>(row) * k + index] * b[static_cast<size_t>(index) * n + col];
    }
    c[static_cast<size_t>(row) * n + col] = sum * scale;
}

FSV_KERNEL void fsv_matmul_i8(const signed char* a, const signed char* b, float* c,
                              int m, int k, int n, float scale) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= m || col >= n) return;
    float sum = 0.0f;
    for (int index = 0; index < k; ++index) {
        sum += static_cast<float>(a[static_cast<size_t>(row) * k + index]) *
               static_cast<float>(b[static_cast<size_t>(index) * n + col]);
    }
    c[static_cast<size_t>(row) * n + col] = sum * scale;
}

FSV_KERNEL void fsv_matmul_i4(const signed char* a, const unsigned char* packed_b, float* c,
                              int m, int k, int n, float scale) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= m || col >= n) return;
    float sum = 0.0f;
    for (int index = 0; index < k; ++index) {
        int linear = index * n + col;
        float weight = static_cast<float>(unpack_i4(packed_b[linear >> 1], (linear & 1) != 0));
        sum += static_cast<float>(a[static_cast<size_t>(row) * k + index]) * weight;
    }
    c[static_cast<size_t>(row) * n + col] = sum * scale;
}

/* ORT MatMulNBits layout: packed B is [N, ceil(K/block_size), block_size/2]. */
FSV_KERNEL void fsv_matmul_nbits_f32(const float* a, const unsigned char* packed_b,
                                     const float* scales, float* c, int m, int k, int n,
                                     int block_size, int bits) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= m || col >= n) return;
    int blocks = (k + block_size - 1) / block_size;
    float sum = 0.0f;
    for (int index = 0; index < k; ++index) {
        int block = index / block_size;
        int offset = index - block * block_size;
        int packed_index = (col * blocks + block) * (block_size * bits / 8) + (offset / (8 / bits));
        unsigned char packed = packed_b[packed_index];
        int nibble = (offset & 1) ? (packed >> 4) : (packed & 0x0f);
        sum += a[static_cast<size_t>(row) * k + index] * static_cast<float>(nibble - 8) *
               scales[col * blocks + block];
    }
    c[static_cast<size_t>(row) * n + col] = sum;
}

FSV_KERNEL void fsv_matmul_nbits_f16scale(const float* a, const unsigned char* packed_b,
                                          const unsigned short* scales, float* c, int m, int k,
                                          int n, int block_size, int bits) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= m || col >= n) return;
    int blocks = (k + block_size - 1) / block_size;
    float sum = 0.0f;
    for (int index = 0; index < k; ++index) {
        int block = index / block_size;
        int offset = index - block * block_size;
        int packed_index = (col * blocks + block) * (block_size * bits / 8) + (offset / (8 / bits));
        unsigned char packed = packed_b[packed_index];
        int nibble = (offset & 1) ? (packed >> 4) : (packed & 0x0f);
        float scale = __half2float(reinterpret_cast<const __half*>(scales)[col * blocks + block]);
        sum += a[static_cast<size_t>(row) * k + index] * static_cast<float>(nibble - 8) * scale;
    }
    c[static_cast<size_t>(row) * n + col] = sum;
}

/* One lane's slice of a packed INT4 weight column.

   The byte-at-a-time form reads a single useful byte per lane per step, so the
   thirty-two lanes of a warp land on thirty-two different cache lines: a
   kilobyte of sectors fetched for thirty-two bytes of weights. A uint4 read
   fills one lane's registers with sixteen packed bytes, which is thirty-two
   values' worth of weights in one transaction.

   The condition is alignment: a vector group has to start on a 16-byte
   boundary. The host keeps each lane's slice a multiple of 32 values whenever
   the shape allows, so the aligned form is the common case, and the values
   before the first aligned group fall back to the byte loop. */
__device__ __forceinline__ void nbits4_accumulate(const unsigned char* packed,
                                                  const float* values, int count,
                                                  float* partial) {
#pragma unroll
    for (int lane = 0; lane < 4; ++lane) partial[lane] = 0.0f;
    int step = 0;
    while (step + 32 <= count &&
           (reinterpret_cast<unsigned long long>(packed + (step >> 1)) & 15ull) != 0) {
        const unsigned char byte = packed[step >> 1];
        partial[0] += values[step] * static_cast<float>((byte & 0x0f) - 8);
        partial[1] += values[step + 1] * static_cast<float>((byte >> 4) - 8);
        step += 2;
    }
    for (; step + 32 <= count; step += 32) {
        const uint4 group = *reinterpret_cast<const uint4*>(packed + (step >> 1));
        const unsigned int words[4] = {group.x, group.y, group.z, group.w};
#pragma unroll
        for (int word = 0; word < 4; ++word) {
            unsigned int bits = words[word];
            const float* chunk = values + step + word * 8;
#pragma unroll
            for (int slot = 0; slot < 8; ++slot) {
                partial[slot & 3] +=
                    chunk[slot] * static_cast<float>(static_cast<int>(bits & 0x0fu) - 8);
                bits >>= 4;
            }
        }
    }
    for (; step + 8 <= count; step += 8) {
#pragma unroll
        for (int lane = 0; lane < 4; ++lane) {
            const unsigned char byte = packed[(step >> 1) + lane];
            partial[lane] += values[step + 2 * lane] *
                             static_cast<float>((byte & 0x0f) - 8);
            partial[lane] += values[step + 2 * lane + 1] *
                             static_cast<float>((byte >> 4) - 8);
        }
    }
    for (; step + 1 < count; step += 2) {
        const unsigned char byte = packed[step >> 1];
        partial[0] += values[step] * static_cast<float>((byte & 0x0f) - 8);
        partial[0] += values[step + 1] * static_cast<float>((byte >> 4) - 8);
    }
    if (step < count) {
        partial[0] += values[step] *
                      static_cast<float>((packed[step >> 1] & 0x0f) - 8);
    }
}

/* INT4 quantized product built around the shape a decoder step really has:
   m == 1 with n in the thousands. The kernel above gives every output element
   one thread, which for a single row leaves only n threads alive, and each of
   them walks k in a dependent chain that ends in a byte load behind two
   integer divisions and a scale lookup per element.

   Here `split` lanes cooperate on one column, each walking one contiguous
   slice of it. A lane's slice never crosses a block boundary without the loop
   noticing, so it applies the block scale once per block instead of once per
   element, and the packed nibbles are read as whole bytes, or sixteen at a
   time once the slice is 16-byte aligned. Lanes of a column then reduce with
   shuffles (split is a power of two, at most 32).

   Inactive lanes keep running: __shfl_down_sync needs the whole warp, so the
   out-of-range lanes compute a duplicate of the last column and simply do not
   store. */
FSV_KERNEL void fsv_matmul_nbits4_f32(const float* a, const unsigned char* packed_b,
                                      const float* scales, const unsigned short* scales_f16,
                                      float* c, int m, int k, int n, int block_size,
                                      int values_per_lane, int split) {
    const int lane = threadIdx.x & 31;
    const int warps_per_block = blockDim.x >> 5;
    const int lanes_per_column = split;
    const int columns_per_warp = 32 / lanes_per_column;
    const long long warp = blockIdx.x * warps_per_block + (threadIdx.x >> 5);
    const long long total = static_cast<long long>(m) * n;
    long long slot = warp * columns_per_warp + lane / lanes_per_column;
    const bool active = slot < total;
    if (!active) slot = total - 1;
    const int row = static_cast<int>(slot / n);
    const int col = static_cast<int>(slot - static_cast<long long>(row) * n);
    const int k_slot = lane % lanes_per_column;
    const int begin = k_slot * values_per_lane;
    float sum = 0.0f;
    if (begin < k && values_per_lane > 0) {
        const int end = min(k, begin + values_per_lane);
        const int blocks = (k + block_size - 1) / block_size;
        const int bytes_per_block = block_size / 2;
        const unsigned char* column =
            packed_b + static_cast<size_t>(col) * blocks * bytes_per_block;
        const float* activation = a + static_cast<size_t>(row) * k;
        int index = begin;
        while (index < end) {
            const int block = index / block_size;
            const int block_begin = block * block_size;
            const int block_end = min(end, block_begin + block_size);
            const unsigned char* packed =
                column + static_cast<size_t>(block) * bytes_per_block +
                ((index - block_begin) >> 1);
            const float* values = activation + index;
            const int count = block_end - index;
            /* Four independent accumulators. A single chain of dependent adds
               leaves the multiply-add units waiting on their own latency, which
               is what held this kernel at a few percent of the card's rate. */
            float partial[4];
            nbits4_accumulate(packed, values, count, partial);
            /* The block scale multiplies every term of the block, so taking it
               out of the loop costs one multiply per block instead of one per
               element and cannot change the result beyond rounding. */
            const float scale = scales_f16
                ? __half2float(reinterpret_cast<const __half*>(scales_f16)[
                      static_cast<size_t>(col) * blocks + block])
                : scales[static_cast<size_t>(col) * blocks + block];
            sum += ((partial[0] + partial[1]) + (partial[2] + partial[3])) * scale;
            index = block_end;
        }
    }
#pragma unroll
    for (int offset = 16; offset > 0; offset >>= 1) {
        if (offset < lanes_per_column) {
            sum += __shfl_down_sync(0xffffffffu, sum, offset);
        }
    }
    if (active && k_slot == 0) {
        c[static_cast<size_t>(slot)] = sum;
    }
}

/* One thread per output position, OC_TILE output channels deep.

   The previous mapping gave every *output element* a thread, so the two
   spatial dimensions were the only source of reuse and each of the
   out_channels passes over the same input plane walked it again: the
   exported 1x1 convolutions are really linear layers (9216 inputs by 2048
   outputs), and re-reading 24 MB of activations per output channel turned
   24.5 GFLOP into 49 GB of DRAM traffic. OC_TILE channels per thread divides
   that by OC_TILE while the weights stay broadcast across the warp, so the
   host picks the widest tile the channel count still divides into. */
template <int OC_TILE>
__device__ __forceinline__ void conv2d_tiled(const float* input, const float* weight,
                                             const float* bias, float* output, int channels,
                                             int height, int width, int out_channels,
                                             int kernel_h, int kernel_w, int pad_top,
                                             int pad_left, int stride_h, int stride_w,
                                             int dilation_h, int dilation_w, int groups,
                                             int out_h, int out_w) {
    const int spatial = out_h * out_w;
    const int position = blockIdx.x * blockDim.x + threadIdx.x;
    if (position >= spatial) return;
    const int oc_base = blockIdx.y * OC_TILE;
    const int nb = blockIdx.z;
    const int ox = position % out_w;
    const int oy = position / out_w;
    const int out_per_group = out_channels / groups;
    const int in_per_group = channels / groups;
    const int group = oc_base / out_per_group;
    float sums[OC_TILE];
#pragma unroll
    for (int tile = 0; tile < OC_TILE; ++tile) {
        sums[tile] = bias ? bias[oc_base + tile] : 0.0f;
    }
    for (int ic = 0; ic < in_per_group; ++ic) {
        const float* plane = input + (static_cast<size_t>(nb) * channels + group * in_per_group + ic) *
                                         height * width;
        const float* weights = weight + (static_cast<size_t>(oc_base) * in_per_group + ic) *
                                            kernel_h * kernel_w;
        for (int ky = 0; ky < kernel_h; ++ky) {
            const int iy = oy * stride_h - pad_top + ky * dilation_h;
            if (iy < 0 || iy >= height) continue;
            const float* row = plane + static_cast<size_t>(iy) * width;
            for (int kx = 0; kx < kernel_w; ++kx) {
                const int ix = ox * stride_w - pad_left + kx * dilation_w;
                if (ix < 0 || ix >= width) continue;
                const float value = row[ix];
                const int offset = ky * kernel_w + kx;
#pragma unroll
                for (int tile = 0; tile < OC_TILE; ++tile) {
                    sums[tile] += value * weights[static_cast<size_t>(tile) * in_per_group *
                                                      kernel_h * kernel_w + offset];
                }
            }
        }
    }
    const size_t base = static_cast<size_t>(nb) * out_channels + oc_base;
#pragma unroll
    for (int tile = 0; tile < OC_TILE; ++tile) {
        output[(base + tile) * spatial + position] = sums[tile];
    }
}

FSV_KERNEL void fsv_conv2d_f32(const float* input, const float* weight, const float* bias,
                               float* output, int batch, int channels, int height, int width,
                               int out_channels, int kernel_h, int kernel_w, int pad_top,
                               int pad_left, int pad_bottom, int pad_right, int stride_h,
                               int stride_w, int dilation_h, int dilation_w, int groups,
                               int out_h, int out_w) {
    (void)batch;
    (void)pad_bottom;
    (void)pad_right;
    conv2d_tiled<1>(input, weight, bias, output, channels, height, width, out_channels,
                    kernel_h, kernel_w, pad_top, pad_left, stride_h, stride_w, dilation_h,
                    dilation_w, groups, out_h, out_w);
}

/* The tile width is a launch parameter rather than extra entry points: the
   channel count decides which of the instantiations is legal, and the host
   has already checked that the block does not straddle a group. */
FSV_KERNEL void fsv_conv2d_f32_tiled(const float* input, const float* weight,
                                     const float* bias, float* output, int batch, int channels,
                                     int height, int width, int out_channels, int kernel_h,
                                     int kernel_w, int pad_top, int pad_left, int pad_bottom,
                                     int pad_right, int stride_h, int stride_w, int dilation_h,
                                     int dilation_w, int groups, int out_h, int out_w,
                                     int oc_tile) {
    (void)batch;
    (void)pad_bottom;
    (void)pad_right;
    if (oc_tile == 16) {
        conv2d_tiled<16>(input, weight, bias, output, channels, height, width, out_channels,
                         kernel_h, kernel_w, pad_top, pad_left, stride_h, stride_w,
                         dilation_h, dilation_w, groups, out_h, out_w);
    } else if (oc_tile == 8) {
        conv2d_tiled<8>(input, weight, bias, output, channels, height, width, out_channels,
                        kernel_h, kernel_w, pad_top, pad_left, stride_h, stride_w,
                        dilation_h, dilation_w, groups, out_h, out_w);
    } else {
        conv2d_tiled<4>(input, weight, bias, output, channels, height, width, out_channels,
                        kernel_h, kernel_w, pad_top, pad_left, stride_h, stride_w,
                        dilation_h, dilation_w, groups, out_h, out_w);
    }
}

/* Flat fallback for shapes the tiled kernel's three-dimensional grid cannot
   express, such as a grouped convolution whose output channels do not form
   blocks of four inside a group. */
FSV_KERNEL void fsv_conv2d_f32_flat(const float* input, const float* weight,
                                    const float* bias, float* output, int batch, int channels,
                                    int height, int width, int out_channels, int kernel_h,
                                    int kernel_w, int pad_top, int pad_left, int pad_bottom,
                                    int pad_right, int stride_h, int stride_w, int dilation_h,
                                    int dilation_w, int groups, int out_h, int out_w) {
    size_t index = static_cast<size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(batch) * out_channels * out_h * out_w;
    if (index >= total) return;
    int ox = static_cast<int>(index % out_w); index /= out_w;
    int oy = static_cast<int>(index % out_h); index /= out_h;
    int oc = static_cast<int>(index % out_channels);
    int nb = static_cast<int>(index / out_channels);
    int out_per_group = out_channels / groups;
    int in_per_group = channels / groups;
    int group = oc / out_per_group;
    float sum = bias ? bias[oc] : 0.0f;
    for (int ic = 0; ic < in_per_group; ++ic) {
        for (int ky = 0; ky < kernel_h; ++ky) {
            int iy = oy * stride_h - pad_top + ky * dilation_h;
            if (iy < 0 || iy >= height) continue;
            for (int kx = 0; kx < kernel_w; ++kx) {
                int ix = ox * stride_w - pad_left + kx * dilation_w;
                if (ix < 0 || ix >= width) continue;
                size_t input_index = ((static_cast<size_t>(nb) * channels + group * in_per_group + ic) * height + iy) * width + ix;
                size_t weight_index = ((static_cast<size_t>(oc) * in_per_group + ic) * kernel_h + ky) * kernel_w + kx;
                sum += input[input_index] * weight[weight_index];
            }
        }
    }
    output[((static_cast<size_t>(nb) * out_channels + oc) * out_h + oy) * out_w + ox] = sum;
}

/* 1-D ConvTranspose, ONNX weight layout [C_in, C_out/groups, K]. */
FSV_KERNEL void fsv_conv_transpose1d_f32(const float* input, const float* weight,
                                         const float* bias, float* output, int batch,
                                         int channels, int width, int out_channels, int kernel,
                                         int pad_left, int stride, int dilation, int groups,
                                         int out_width) {
    size_t linear = static_cast<size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    size_t total = static_cast<size_t>(batch) * out_channels * out_width;
    if (linear >= total) return;
    int ox = static_cast<int>(linear % out_width);
    int oc = static_cast<int>((linear / out_width) % out_channels);
    int nb = static_cast<int>(linear / (static_cast<size_t>(out_channels) * out_width));
    int out_per_group = out_channels / groups;
    int in_per_group = channels / groups;
    int group = oc / out_per_group;
    int in_begin = group * in_per_group;
    int in_end = in_begin + in_per_group;
    float value = bias ? bias[oc] : 0.0f;
    /* The output position is what decides whether a tap lands on this sample:
       `(ox + pad_left - k * dilation) % stride` does not mention the input
       channel at all. Running the tap loop outside the channel loop turns a
       division per (input channel, tap) pair into one per tap, which for the
       vocoder's upsampling layers is a five-hundredfold cut in the integer
       division the kernel was built around. */
    const int weight_oc = oc - group * out_per_group;
    for (int k = 0; k < kernel; ++k) {
        const int numerator = ox + pad_left - k * dilation;
        if (numerator < 0 || numerator % stride != 0) continue;
        const int ix = numerator / stride;
        if (ix < 0 || ix >= width) continue;
        const float* source = input + static_cast<size_t>(nb) * channels * width + ix;
        const float* taps = weight + static_cast<size_t>(in_begin) * out_per_group * kernel +
                            weight_oc * kernel + k;
        for (int ic = in_begin; ic < in_end; ++ic) {
            value += source[static_cast<size_t>(ic) * width] *
                     taps[static_cast<size_t>(ic - in_begin) * out_per_group * kernel];
        }
    }
    output[linear] = value;
}


/* ========================================================================
   Residency kernels.

   The graph runtime keeps whole tensors in device memory, so these take
   device pointers it owns and never touch system memory. They are launched
   asynchronously; every copy on the default stream is ordered after them.
   ======================================================================== */

#define FSV_MAX_RANK 8

/* Argument block shared with cuda_ops.cpp. It is passed by value so a launch
   needs no extra upload for the index tables.
   rank == 0 means "both operands are contiguous with the output shape". */
typedef struct FsvIndex {
    long long shape[FSV_MAX_RANK];
    long long a_stride[FSV_MAX_RANK];
    long long b_stride[FSV_MAX_RANK];
    int rank;
    int operation;
} FsvIndex;

__device__ __forceinline__ void fsv_load_index(const FsvIndex& index,
                                               long long* shape,
                                               long long* a_stride,
                                               long long* b_stride) {
#pragma unroll
    for (int dim = 0; dim < FSV_MAX_RANK; ++dim) {
        shape[dim] = index.shape[dim];
        a_stride[dim] = index.a_stride[dim];
        b_stride[dim] = index.b_stride[dim];
    }
}

__device__ __forceinline__ float fsv_binary_apply(int operation, float x, float y) {
    switch (operation) {
        case 0: return x + y;
        case 1: return x * y;
        case 2: return x - y;
        case 3: return x / y;
        case 4: return powf(x, y);
        case 5: return fmaxf(x, y);
        case 6: return fminf(x, y);
        case 7: return copysignf(1.0f, x); /* unreachable, keeps nvcc happy */
        default: return x;
    }
}

/* Coordinate walk used by the broadcast, transpose and box-copy kernels.

   Two things make this the hot loop of those kernels: the division is a
   software sequence rather than an instruction, and the values were 64-bit.
   The offsets are unsigned 32-bit — a tensor that needs more than four
   billion elements cannot exist in the device memory this runtime targets —
   and a dimension of size one contributes a zero coordinate, so it is skipped
   entirely instead of paying a division for a result known to be zero. */
__device__ __forceinline__ void fsv_walk_coordinates(const FsvIndex& index,
                                                     unsigned long long flat,
                                                     long long* a_offset,
                                                     long long* b_offset) {
    long long shape[FSV_MAX_RANK];
    long long a_stride[FSV_MAX_RANK];
    long long b_stride[FSV_MAX_RANK];
    fsv_load_index(index, shape, a_stride, b_stride);
    unsigned int rest = static_cast<unsigned int>(flat);
    long long a_total = 0;
    long long b_total = 0;
    for (int dim = index.rank - 1; dim >= 0; --dim) {
        const unsigned int size = static_cast<unsigned int>(shape[dim]);
        if (size > 1) {
            const unsigned int coordinate = rest % size;
            rest /= size;
            a_total += static_cast<long long>(coordinate) * a_stride[dim];
            b_total += static_cast<long long>(coordinate) * b_stride[dim];
        }
    }
    *a_offset = a_total;
    *b_offset = b_total;
}

FSV_KERNEL void fsv_binary_bcast_f32(const float* a, const float* b, float* c,
                                     unsigned long long count, FsvIndex index) {
    unsigned long long flat = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    if (index.rank == 0) {
        c[flat] = fsv_binary_apply(index.operation, a[flat], b[flat]);
        return;
    }
    long long a_offset = 0;
    long long b_offset = 0;
    fsv_walk_coordinates(index, flat, &a_offset, &b_offset);
    c[flat] = fsv_binary_apply(index.operation, a[a_offset], b[b_offset]);
}

/* Element-wise operator on two operands that are both laid out exactly like
   the result: no broadcast and no coordinate walk, so four elements fit in one
   float4 load and store. The exported graphs are dominated by those shapes
   ([1,1,2048] op [1,1,2048] and friends), where the walk cost more than the
   arithmetic. */
FSV_KERNEL void fsv_binary_contiguous_f32(const float* a, const float* b, float* c,
                                          unsigned long long count, int operation) {
    const unsigned long long thread =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long start = thread * 4ull;
    if (start + 4ull <= count) {
        const float4 x = *reinterpret_cast<const float4*>(a + start);
        const float4 y = *reinterpret_cast<const float4*>(b + start);
        float4 result;
        result.x = fsv_binary_apply(operation, x.x, y.x);
        result.y = fsv_binary_apply(operation, x.y, y.y);
        result.z = fsv_binary_apply(operation, x.z, y.z);
        result.w = fsv_binary_apply(operation, x.w, y.w);
        *reinterpret_cast<float4*>(c + start) = result;
        return;
    }
    for (unsigned long long index = start; index < count; ++index) {
        c[index] = fsv_binary_apply(operation, a[index], b[index]);
    }
}

/* One operand is a single value. The broadcast kernel walks the coordinate
   index once per element just to read that same value back; here the scalar is
   loaded once per thread and four outputs move per thread. `scalar_first` says
   which side of the operator the single value belongs to, because division and
   pow are not commutative. */
FSV_KERNEL void fsv_binary_scalar_f32(const float* operand, const float* scalar, float* out,
                                      unsigned long long count, int operation,
                                      int scalar_first) {
    const unsigned long long thread =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long start = thread * 4ull;
    const float value = scalar[0];
    if (start + 4ull <= count) {
        const float4 x = *reinterpret_cast<const float4*>(operand + start);
        float4 result;
        result.x = scalar_first ? fsv_binary_apply(operation, value, x.x)
                                : fsv_binary_apply(operation, x.x, value);
        result.y = scalar_first ? fsv_binary_apply(operation, value, x.y)
                                : fsv_binary_apply(operation, x.y, value);
        result.z = scalar_first ? fsv_binary_apply(operation, value, x.z)
                                : fsv_binary_apply(operation, x.z, value);
        result.w = scalar_first ? fsv_binary_apply(operation, value, x.w)
                                : fsv_binary_apply(operation, x.w, value);
        *reinterpret_cast<float4*>(out + start) = result;
        return;
    }
    for (unsigned long long index = start; index < count; ++index) {
        out[index] = scalar_first ? fsv_binary_apply(operation, value, operand[index])
                                  : fsv_binary_apply(operation, operand[index], value);
    }
}

FSV_KERNEL void fsv_compare_bcast_u8(const float* a, const float* b, unsigned char* c,
                                     unsigned long long count, FsvIndex index) {
    unsigned long long flat = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    long long a_offset = flat;
    long long b_offset = flat;
    if (index.rank > 0) {
        fsv_walk_coordinates(index, flat, &a_offset, &b_offset);
    }
    float x = a[a_offset];
    float y = b[b_offset];
    unsigned char value = 0;
    if (index.operation == 0) value = x == y ? 1 : 0;
    else if (index.operation == 1) value = x < y ? 1 : 0;
    else value = x > y ? 1 : 0;
    c[flat] = value;
}

FSV_KERNEL void fsv_unary_ops_f32(const float* a, float* c, unsigned long long count,
                                  int operation, float alpha) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    float value = a[index];
    switch (operation) {
        case 0: c[index] = -value; break;
        case 1: c[index] = sinf(value); break;
        case 2: c[index] = cosf(value); break;
        case 3: c[index] = sqrtf(value); break;
        case 4: c[index] = erff(value); break;
        case 5: c[index] = tanhf(value); break;
        case 6: c[index] = 1.0f / (1.0f + expf(-value)); break;
        case 7: c[index] = expf(value); break;
        case 8: c[index] = logf(value); break;
        case 9: c[index] = log1pf(expf(value)); break;
        case 10: c[index] = fmaxf(value, 0.0f); break;
        case 11: c[index] = value >= 0.0f ? value : alpha * value; break;
        case 12: c[index] = floorf(value); break;
        default: c[index] = value; break;
    }
}

FSV_KERNEL void fsv_clamp_f32(const float* a, float* c, unsigned long long count,
                              int has_low, float low, int has_high, float high) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    float value = a[index];
    if (has_low && value < low) value = low;
    if (has_high && value > high) value = high;
    c[index] = value;
}

/* BatchNormalization folded into a per-channel scale and shift. */
FSV_KERNEL void fsv_channel_affine_f32(const float* input, const float* scale,
                                       const float* shift, float* output,
                                       long long batch, long long channels, long long spatial) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    unsigned long long total = static_cast<unsigned long long>(batch) * channels * spatial;
    if (index >= total) return;
    long long channel = static_cast<long long>((index / static_cast<unsigned long long>(spatial)) % static_cast<unsigned long long>(channels));
    output[index] = input[index] * (scale ? scale[channel] : 1.0f) +
                    (shift ? shift[channel] : 0.0f);
}

FSV_KERNEL void fsv_transpose_f32(const float* source, float* destination,
                                  unsigned long long count, int rank, FsvIndex index) {
    /* index.shape is the output shape, index.a_stride the source strides in
       output dimension order. The rank travels as its own argument because
       that is the public entry point's signature, so it is folded back into
       the index the coordinate walk reads. */
    index.rank = rank;
    unsigned long long flat = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    long long source_offset = 0;
    long long ignored = 0;
    fsv_walk_coordinates(index, flat, &source_offset, &ignored);
    destination[flat] = source[source_offset];
}

/* Batched two-dimensional transpose of a rectangular block: source [rows][cols]
   to destination [cols][rows], repeated over blockIdx.z.

   The generic walk above gives every element one thread and reads the source
   with a stride of `cols`, so a warp touches 32 different cache lines and the
   kernel runs at a few percent of the card's bandwidth. Staging one 32x32 tile
   through shared memory makes the load and the store coalesced, which is the
   difference between a 512x512 transpose costing ten microseconds and costing
   a hundred. The extra column of padding is what keeps the transposed read
   out of bank conflicts. */
FSV_KERNEL void fsv_transpose_tile_f32(const float* source, float* destination,
                                       long long rows, long long cols) {
    __shared__ float tile[32][33];
    const float* src = source + static_cast<size_t>(blockIdx.z) * rows * cols;
    float* dst = destination + static_cast<size_t>(blockIdx.z) * rows * cols;
    const long long bx = static_cast<long long>(blockIdx.x) * 32;
    const long long by = static_cast<long long>(blockIdx.y) * 32;
    const int tx = threadIdx.x;
    const int ty = threadIdx.y;
    if (bx + tx < cols && by + ty < rows) {
        tile[ty][tx] = src[(by + ty) * cols + bx + tx];
    }
    __syncthreads();
    if (bx + ty < cols && by + tx < rows) {
        dst[(bx + ty) * rows + by + tx] = tile[tx][ty];
    }
}

/* Swap of two tensor axes that carry a contiguous run of `inner` values each.

   Everything before the first swapped axis is a batch, so the source is
   [outer][a_size][b_size][inner] and the destination is the same block with
   the two middle axes exchanged. Both runs of `inner` values are contiguous in
   memory, so a thread copies a whole run: consecutive threads then write
   consecutive addresses while reading runs that are one cache line or more
   apart, which keeps the store coalesced. The inner == 1 case is a plain
   matrix transpose and belongs to the tiled kernel above instead. */
FSV_KERNEL void fsv_transpose_swap_f32(const float* source, float* destination,
                                       long long a_size, long long b_size,
                                       long long inner) {
    const long long slot = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const long long total = a_size * b_size;
    if (slot >= total) return;
    const long long a = slot % a_size;
    const long long rest = slot / a_size;
    const long long b = rest % b_size;
    const long long outer = rest / b_size;
    const float* from = source + ((outer * a_size + a) * b_size + b) * inner;
    float* to = destination + slot * inner;
    if ((inner & 3) == 0) {
        const float4* vector_from = reinterpret_cast<const float4*>(from);
        float4* vector_to = reinterpret_cast<float4*>(to);
        const long long vectors = inner >> 2;
        for (long long index = 0; index < vectors; ++index) vector_to[index] = vector_from[index];
        return;
    }
    for (long long index = 0; index < inner; ++index) to[index] = from[index];
}

/* Strided copy of a box: used by Concat, Slice, Split, Tile and Expand. Both
   operands are contiguous at tensor level, so plain element strides suffice. */
FSV_KERNEL void fsv_copy_nd_f32(const float* source, float* destination,
                                unsigned long long count, long long source_base,
                                long long destination_base, int rank, FsvIndex index) {
    unsigned long long flat = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    if (rank == 0) {
        destination[destination_base + flat] = source[source_base + flat];
        return;
    }
    index.rank = rank;
    long long source_offset = 0;
    long long destination_offset = 0;
    fsv_walk_coordinates(index, flat, &source_offset, &destination_offset);
    destination[destination_base + destination_offset] =
        source[source_base + source_offset];
}

/* Contiguous run-to-run copy. Slice, Concat and Split all move boxes that are
   contiguous at both ends -- a trailing slice or one whole operand of a
   concatenation -- and those need neither the coordinate walk nor one thread
   per element. */
FSV_KERNEL void fsv_copy_contiguous_f32(const float* source, float* destination,
                                        unsigned long long count) {
    const unsigned long long thread =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long start = thread * 4ull;
    if (start + 4ull <= count) {
        *reinterpret_cast<float4*>(destination + start) =
            *reinterpret_cast<const float4*>(source + start);
        return;
    }
    for (unsigned long long index = start; index < count; ++index) {
        destination[index] = source[index];
    }
}

/* Reduce one contiguous run of dimensions: [outer][mid][inner] to [outer][inner].
   operation 0 sums, 1 averages, 2 takes the maximum and 3 the L2 norm. The
   host only routes a node here when its axes name such a run, so the kernel
   needs no coordinate walk at all. */
FSV_KERNEL void fsv_reduce_block_f32(const float* input, float* output,
                                     long long outer, long long mid, long long inner,
                                     int operation) {
    const long long slot = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const long long total = outer * inner;
    if (slot >= total || mid <= 0) return;
    const long long row = slot / inner;
    const long long column = slot % inner;
    const float* base = input + row * mid * inner + column;
    if (operation == 2) {
        float best = -CUDART_INF_F;
        for (long long step = 0; step < mid; ++step) {
            best = fmaxf(best, base[step * inner]);
        }
        output[slot] = best;
        return;
    }
    /* The host reduce accumulated in double and rounded once at the end;
       keeping that here stops a long decode chain from drifting. */
    double accumulator = 0.0;
    for (long long step = 0; step < mid; ++step) {
        const double value = static_cast<double>(base[step * inner]);
        accumulator += operation == 3 ? value * value : value;
    }
    if (operation == 1) accumulator /= static_cast<double>(mid);
    else if (operation == 3) accumulator = sqrt(accumulator);
    output[slot] = static_cast<float>(accumulator);
}

/* Multiply a run of floats by a scalar that travels as a kernel argument. The
   graph used to build a one-element tensor, upload it and run a broadcast pass
   for every Gemm it evaluated; the factor costs nothing here. */
FSV_KERNEL void fsv_scale_into_f32(const float* source, float* destination,
                                   unsigned long long count, float factor) {
    const unsigned long long index =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    destination[index] = source[index] * factor;
}

FSV_KERNEL void fsv_fill_f32(float* destination, unsigned long long count, float value) {
    unsigned long long index = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    destination[index] = value;
}

/* Batched matrix product, one launch for a whole stack of slices.

   The graph runtime used to issue one fsv_matmul_f32 launch per batch slice,
   which the profiler showed as ~48k launches inside 3.2k MatMul nodes. This
   kernel keeps the per-thread work of fsv_matmul_f32 and moves the slice onto
   blockIdx.z: index.shape is the broadcast batch shape and a_stride/b_stride
   are element strides in output batch order, already scaled to whole m*k and
   k*n matrices, with a zero stride broadcasting a dimension. blockIdx.z is
   limited to 65535, so the host splits a larger stack into chunks and passes
   the first slice of each chunk in slice_offset.

   The reduction is not split across lanes: the weight is k-major, so a warp
   reads a contiguous run of columns at each step and splitting k would make
   every one of those reads a separate sector. The parallelism comes from
   kMatmulColumns output columns per thread instead. Four adjacent columns are
   four adjacent floats, so one float4 load per reduction step feeds four
   multiply-adds, and unrolling four steps gives sixteen independent chains in
   place of the single dependent one. The sum order is therefore unchanged for
   the vector path and the tolerance of the reference holds. */
constexpr int kMatmulColumns = 4;

FSV_KERNEL void fsv_matmul_f32_batched(const float* a, const float* b, float* c,
                                       int m, int k, int n, long long batch,
                                       long long slice_offset, FsvIndex index) {
    long long slice = slice_offset + blockIdx.z;
    long long a_base = 0;
    long long b_base = 0;
    if (slice >= batch) return;
    if (index.rank > 0) {
        long long shape[FSV_MAX_RANK];
        long long a_stride[FSV_MAX_RANK];
        long long b_stride[FSV_MAX_RANK];
        fsv_load_index(index, shape, a_stride, b_stride);
        unsigned long long remaining = static_cast<unsigned long long>(slice);
        for (int dim = index.rank - 1; dim >= 0; --dim) {
            long long size = shape[dim];
            long long coordinate =
                size > 0 ? static_cast<long long>(remaining % static_cast<unsigned long long>(size)) : 0;
            remaining = size > 0 ? remaining / static_cast<unsigned long long>(size) : 0;
            a_base += coordinate * a_stride[dim];
            b_base += coordinate * b_stride[dim];
        }
    }
    /* One thread owns kMatmulColumns adjacent columns of one row. A quad never
       spans two rows: the group index is per row, so the columns a thread
       reads are adjacent in the k-major weight. */
    const long long groups_per_row =
        (static_cast<long long>(n) + kMatmulColumns - 1) / kMatmulColumns;
    const long long group = static_cast<long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (group >= static_cast<long long>(m) * groups_per_row) return;
    const int row = static_cast<int>(group / groups_per_row);
    const int col = static_cast<int>(group - static_cast<long long>(row) * groups_per_row) *
                    kMatmulColumns;
    const int usable = min(kMatmulColumns, n - col);
    const float* a_row = a + a_base + static_cast<size_t>(row) * k;
    const float* b_column = b + b_base + col;
    /* [reduction step mod 4][column] */
    float sums[4][kMatmulColumns];
#pragma unroll
    for (int slot = 0; slot < 4; ++slot) {
#pragma unroll
        for (int column = 0; column < kMatmulColumns; ++column) sums[slot][column] = 0.0f;
    }
    int step = 0;
    /* The vector path needs both operands 16-byte aligned: the activation row
       on a four-value boundary (k a multiple of four) and the weight quad on
       one (n a multiple of four), which is what keeps a row from being
       re-read out of alignment. */
    if ((k & 3) == 0 && (n & 3) == 0 && usable == kMatmulColumns) {
        for (; step + 4 <= k; step += 4) {
            const float4 activation = *reinterpret_cast<const float4*>(a_row + step);
            const float4 b0 = *reinterpret_cast<const float4*>(
                b_column + static_cast<size_t>(step + 0) * n);
            const float4 b1 = *reinterpret_cast<const float4*>(
                b_column + static_cast<size_t>(step + 1) * n);
            const float4 b2 = *reinterpret_cast<const float4*>(
                b_column + static_cast<size_t>(step + 2) * n);
            const float4 b3 = *reinterpret_cast<const float4*>(
                b_column + static_cast<size_t>(step + 3) * n);
            sums[0][0] += activation.x * b0.x;
            sums[0][1] += activation.x * b0.y;
            sums[0][2] += activation.x * b0.z;
            sums[0][3] += activation.x * b0.w;
            sums[1][0] += activation.y * b1.x;
            sums[1][1] += activation.y * b1.y;
            sums[1][2] += activation.y * b1.z;
            sums[1][3] += activation.y * b1.w;
            sums[2][0] += activation.z * b2.x;
            sums[2][1] += activation.z * b2.y;
            sums[2][2] += activation.z * b2.z;
            sums[2][3] += activation.z * b2.w;
            sums[3][0] += activation.w * b3.x;
            sums[3][1] += activation.w * b3.y;
            sums[3][2] += activation.w * b3.z;
            sums[3][3] += activation.w * b3.w;
        }
    }
    for (; step < k; ++step) {
        const float value = a_row[step];
        const float* weights = b_column + static_cast<size_t>(step) * n;
#pragma unroll
        for (int column = 0; column < kMatmulColumns; ++column) {
            if (column < usable) sums[step & 3][column] += value * weights[column];
        }
    }
    float* destination =
        c + static_cast<size_t>(slice) * m * n + static_cast<size_t>(row) * n + col;
#pragma unroll
    for (int column = 0; column < kMatmulColumns; ++column) {
        if (column < usable) {
            destination[column] = (sums[0][column] + sums[1][column]) +
                                  (sums[2][column] + sums[3][column]);
        }
    }
}

/* Single-row matrix product, a matrix times a vector: every one of the
   decoder's cached steps is m == 1, and those steps are most of the run.

   One thread per output column leaves the device idle, because the row is a
   single row and the column count is all the parallelism the product has:
   [1, 2048] x [2048, 512] is 512 outputs, sixteen warps, while the weight it
   streams is four megabytes. Splitting the reduction across the warps of a
   block raises the thread count by the split factor, and each warp still reads
   a contiguous 32-column run at every step, so the weight traffic stays
   coalesced -- which a split inside the warp would destroy, since the lanes of
   a warp would then walk different rows of the k-major weight.

   The eight partial sums of a column are folded through shared memory once
   per block instead of a shuffle chain per column. */
constexpr int kGemvWarps = 8;
constexpr int kGemvColumns = 32;

FSV_KERNEL void fsv_matmul_f32_gemv(const float* a, const float* b, float* c,
                                    int m, int k, int n, long long batch,
                                    long long slice_offset, FsvIndex index) {
    long long slice = slice_offset + blockIdx.z;
    long long a_base = 0;
    long long b_base = 0;
    if (slice >= batch) return;
    if (index.rank > 0) {
        long long shape[FSV_MAX_RANK];
        long long a_stride[FSV_MAX_RANK];
        long long b_stride[FSV_MAX_RANK];
        fsv_load_index(index, shape, a_stride, b_stride);
        unsigned long long remaining = static_cast<unsigned long long>(slice);
        for (int dim = index.rank - 1; dim >= 0; --dim) {
            long long size = shape[dim];
            long long coordinate =
                size > 0 ? static_cast<long long>(remaining % static_cast<unsigned long long>(size)) : 0;
            remaining = size > 0 ? remaining / static_cast<unsigned long long>(size) : 0;
            a_base += coordinate * a_stride[dim];
            b_base += coordinate * b_stride[dim];
        }
    }
    const int warp = static_cast<int>(threadIdx.x) >> 5;
    const int lane = static_cast<int>(threadIdx.x) & 31;
    const int column = static_cast<int>(blockIdx.x) * kGemvColumns + lane;
    /* The padding keeps the fold from walking one bank eight times. */
    __shared__ float partial[kGemvWarps][kGemvColumns + 1];
    float sum = 0.0f;
    if (column < n) {
        const float* a_row = a + a_base;
        const float* b_column = b + b_base + column;
        /* This warp owns one contiguous slice of the reduction; the four
           accumulators break the dependency chain of the walk. */
        const int chunk = (k + kGemvWarps - 1) / kGemvWarps;
        int step = warp * chunk;
        const int end = min(k, step + chunk);
        float sums[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        for (; step + 4 <= end; step += 4) {
            sums[0] += a_row[step + 0] * b_column[static_cast<size_t>(step + 0) * n];
            sums[1] += a_row[step + 1] * b_column[static_cast<size_t>(step + 1) * n];
            sums[2] += a_row[step + 2] * b_column[static_cast<size_t>(step + 2) * n];
            sums[3] += a_row[step + 3] * b_column[static_cast<size_t>(step + 3) * n];
        }
        for (; step < end; ++step) {
            sums[0] += a_row[step] * b_column[static_cast<size_t>(step) * n];
        }
        sum = (sums[0] + sums[1]) + (sums[2] + sums[3]);
    }
    partial[warp][lane] = sum;
    __syncthreads();
    if (warp == 0 && column < n) {
        float total = 0.0f;
#pragma unroll
        for (int which = 0; which < kGemvWarps; ++which) total += partial[which][lane];
        c[static_cast<size_t>(slice) * m * n + column] = total;
    }
}

/* Gather along one axis with a contiguous data tensor. */
FSV_KERNEL void fsv_gather_f32(const float* data, const long long* indices,
                               float* output, long long outer, long long index_count,
                               long long inner, long long axis_size) {
    unsigned long long total = static_cast<unsigned long long>(outer) *
        static_cast<unsigned long long>(index_count) *
        static_cast<unsigned long long>(inner);
    unsigned long long flat = static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= total || total == 0) return;
    long long position = static_cast<long long>(flat % static_cast<unsigned long long>(inner));
    unsigned long long rest = flat / static_cast<unsigned long long>(inner);
    long long which = static_cast<long long>(rest % static_cast<unsigned long long>(index_count));
    long long row = static_cast<long long>(rest / static_cast<unsigned long long>(index_count));
    long long selected = indices[which];
    if (selected < 0) selected += axis_size;
    if (selected < 0 || selected >= axis_size) selected = 0;
    output[flat] = data[(row * axis_size + selected) * inner + position];
}

/* GatherElements: the output has the shape of the index tensor and one index
   per output slot picks a position along `axis`, while every other coordinate
   comes from the slot itself. a_stride is the data tensor's stride in output
   dimension order, so the walk is the broadcast walk minus the coordinate the
   index supplies. */
FSV_KERNEL void fsv_gather_elements_f32(const float* data, const long long* indices,
                                        float* output, unsigned long long count,
                                        long long axis, long long axis_size,
                                        FsvIndex index) {
    const unsigned long long flat =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    long long shape[FSV_MAX_RANK];
    long long data_stride[FSV_MAX_RANK];
    long long output_stride[FSV_MAX_RANK];
    fsv_load_index(index, shape, data_stride, output_stride);
    unsigned int rest = static_cast<unsigned int>(flat);
    long long offset = 0;
    for (int dim = index.rank - 1; dim >= 0; --dim) {
        const unsigned int size = static_cast<unsigned int>(shape[dim]);
        if (size > 1) {
            const unsigned int coordinate = rest % size;
            rest /= size;
            if (dim != axis) {
                offset += static_cast<long long>(coordinate) * data_stride[dim];
            }
        }
    }
    long long selected = indices[flat];
    if (selected < 0) selected += axis_size;
    if (selected < 0 || selected >= axis_size) selected = 0;
    output[flat] = data[offset + selected * data_stride[axis]];
}

/* Where(condition, a, b): the result takes a where the condition is non-zero
   and b otherwise. The condition is a byte tensor, so four condition bytes
   travel with four results, and the two operands take the same broadcast walk
   as the binary kernels.

   This node ran on the host before, which forced a full pipeline drain once
   per generated frame: the decoder builds its attention mask with it. */
typedef struct FsvWhereIndex {
    long long shape[FSV_MAX_RANK];
    long long condition_stride[FSV_MAX_RANK];
    long long a_stride[FSV_MAX_RANK];
    long long b_stride[FSV_MAX_RANK];
    int rank;
    int reserved;
} FsvWhereIndex;

__device__ __forceinline__ void fsv_walk_where(const FsvWhereIndex& index,
                                               unsigned long long flat,
                                               long long* c_offset,
                                               long long* a_offset,
                                               long long* b_offset) {
    unsigned int rest = static_cast<unsigned int>(flat);
    long long c_total = 0;
    long long a_total = 0;
    long long b_total = 0;
    for (int dim = index.rank - 1; dim >= 0; --dim) {
        const unsigned int size = static_cast<unsigned int>(index.shape[dim]);
        if (size > 1) {
            const unsigned int coordinate = rest % size;
            rest /= size;
            c_total += static_cast<long long>(coordinate) * index.condition_stride[dim];
            a_total += static_cast<long long>(coordinate) * index.a_stride[dim];
            b_total += static_cast<long long>(coordinate) * index.b_stride[dim];
        }
    }
    *c_offset = c_total;
    *a_offset = a_total;
    *b_offset = b_total;
}

/* All three operands laid out like the result: four results and four condition
   bytes per thread, which is the decoder's mask build. */
FSV_KERNEL void fsv_where_contiguous_f32(const unsigned char* condition, const float* a,
                                         const float* b, float* out,
                                         unsigned long long count) {
    const unsigned long long thread =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long start = thread * 4ull;
    if (start + 4ull <= count) {
        const uchar4 c = *reinterpret_cast<const uchar4*>(condition + start);
        const float4 x = *reinterpret_cast<const float4*>(a + start);
        const float4 y = *reinterpret_cast<const float4*>(b + start);
        float4 result;
        result.x = c.x ? x.x : y.x;
        result.y = c.y ? x.y : y.y;
        result.z = c.z ? x.z : y.z;
        result.w = c.w ? x.w : y.w;
        *reinterpret_cast<float4*>(out + start) = result;
        return;
    }
    for (unsigned long long index = start; index < count; ++index) {
        out[index] = condition[index] ? a[index] : b[index];
    }
}

__device__ __forceinline__ float fsv_where_pick(unsigned char condition, float operand,
                                                float value, int scalar_is_left) {
    if (scalar_is_left) return condition ? value : operand;
    return condition ? operand : value;
}

/* One operand is a single value broadcast over the condition, which is how the
   self-attention masks pick a fill value. The generic walk would only re-read
   that same value, so it is loaded once per thread. */
FSV_KERNEL void fsv_where_scalar_f32(const unsigned char* condition, const float* operand,
                                     const float* scalar, float* out,
                                     unsigned long long count, int scalar_is_left) {
    const unsigned long long thread =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    const unsigned long long start = thread * 4ull;
    const float value = scalar[0];
    if (start + 4ull <= count) {
        const uchar4 c = *reinterpret_cast<const uchar4*>(condition + start);
        const float4 x = *reinterpret_cast<const float4*>(operand + start);
        float4 result;
        result.x = fsv_where_pick(c.x, x.x, value, scalar_is_left);
        result.y = fsv_where_pick(c.y, x.y, value, scalar_is_left);
        result.z = fsv_where_pick(c.z, x.z, value, scalar_is_left);
        result.w = fsv_where_pick(c.w, x.w, value, scalar_is_left);
        *reinterpret_cast<float4*>(out + start) = result;
        return;
    }
    for (unsigned long long index = start; index < count; ++index) {
        out[index] = fsv_where_pick(condition[index], operand[index], value, scalar_is_left);
    }
}

FSV_KERNEL void fsv_where_bcast_f32(const unsigned char* condition, const float* a,
                                    const float* b, float* out, unsigned long long count,
                                    FsvWhereIndex index) {
    const unsigned long long flat =
        static_cast<unsigned long long>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (flat >= count) return;
    if (index.rank == 0) {
        out[flat] = condition[flat] ? a[flat] : b[flat];
        return;
    }
    long long c_offset = 0;
    long long a_offset = 0;
    long long b_offset = 0;
    fsv_walk_where(index, flat, &c_offset, &a_offset, &b_offset);
    out[flat] = condition[c_offset] ? a[a_offset] : b[b_offset];
}
