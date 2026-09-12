/* Self-contained check of the driver-only CUDA runtime: no Python, no ONNX
   Runtime, no CUDA toolkit on the running machine. Every case compares the
   self-written kernel against a scalar CPU reference. */

#include "fsv_cuda_voice_runtime.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace {

int g_failures = 0;

void report(const char* name, bool ok, const std::string& detail) {
    std::printf("%-28s %s  %s\n", name, ok ? "PASS" : "FAIL", detail.c_str());
    if (!ok) ++g_failures;
}

std::vector<float> random_values(std::size_t count, unsigned seed) {
    std::vector<float> values(count);
    unsigned state = seed;
    for (std::size_t index = 0; index < count; ++index) {
        state = state * 1664525u + 1013904223u;
        values[index] = static_cast<float>((state >> 8) % 2000) / 1000.0f - 1.0f;
    }
    return values;
}

double max_abs_error(const std::vector<float>& left, const std::vector<float>& right) {
    double worst = 0.0;
    for (std::size_t index = 0; index < left.size(); ++index) {
        worst = std::fmax(worst, std::fabs(static_cast<double>(left[index]) - right[index]));
    }
    return worst;
}

bool close_enough(const std::vector<float>& left, const std::vector<float>& right,
                  double tolerance) {
    return left.size() == right.size() && max_abs_error(left, right) <= tolerance;
}

std::string error_detail() {
    const char* text = fsv_cuda_last_error();
    return text && *text ? text : "ok";
}

void check_device() {
    int count = fsv_cuda_device_count();
    if (count <= 0) {
        report("device", false, "没有检测到 NVIDIA 设备");
        return;
    }
    fsv_cuda_device_info info{};
    if (fsv_cuda_get_device_info(0, &info) != 0) {
        report("device", false, error_detail());
        return;
    }
    char detail[320];
    std::snprintf(detail, sizeof(detail), "%s sm_%d%d, %.1f GB", info.name, info.major,
                  info.minor, static_cast<double>(info.global_memory_bytes) / 1073741824.0);
    report("device", true, detail);

    /* Every card has to be describable, because that listing is what a refused
       selection prints to explain itself. */
    bool all_described = count > 0;
    for (int index = 0; index < count && all_described; ++index) {
        fsv_cuda_device_info entry{};
        all_described = fsv_cuda_get_device_info(index, &entry) == 0 && entry.name[0] != '\0';
    }
    report("device_info_all", all_described, std::to_string(count) + " device(s)");

    /* Selection may pick any index, not necessarily 0, and the caller has to be
       able to say which card the "NVIDIA acceleration" switch landed on. */
    char selected[320] = "";
    const int active = fsv_cuda_active_device(selected, sizeof(selected));
    char selection_detail[400];
    std::snprintf(selection_detail, sizeof(selection_detail), "index %d: %s", active,
                  selected[0] ? selected : error_detail().c_str());
    report("device_select", active >= 0 && active < count && selected[0] != '\0',
           selection_detail);

    /* The "give up on the card" flag has to be readable and resettable before
       any graph runs: the host asks it to decide whether to keep sending work
       to the device or run the rest of the synthesis on the CPU baseline. */
    fsv_cuda_reset_device_abandoned();
    report("device_abandon_flag", fsv_cuda_device_abandoned() == 0,
           "reset clears the abandon flag");
}

void check_matmul() {
    const int m = 7;
    const int k = 5;
    const int n = 6;
    std::vector<float> a = random_values(static_cast<std::size_t>(m) * k, 1u);
    std::vector<float> b = random_values(static_cast<std::size_t>(k) * n, 2u);
    std::vector<float> expected(static_cast<std::size_t>(m) * n, 0.0f);
    for (int row = 0; row < m; ++row) {
        for (int col = 0; col < n; ++col) {
            float sum = 0.0f;
            for (int index = 0; index < k; ++index) {
                sum += a[static_cast<std::size_t>(row) * k + index] *
                       b[static_cast<std::size_t>(index) * n + col];
            }
            expected[static_cast<std::size_t>(row) * n + col] = sum;
        }
    }
    std::vector<float> actual(static_cast<std::size_t>(m) * n, 0.0f);
    int status = fsv_cuda_matmul_f32_host(a.data(), b.data(), actual.data(), m, k, n);
    report("matmul_f32", status == 0 && close_enough(expected, actual, 1e-4),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* Only the scale values this test uses, all of them exact in fp16. */
unsigned short to_half_bits(float value) {
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    const std::uint32_t sign = (bits >> 16) & 0x8000u;
    const int exponent = static_cast<int>((bits >> 23) & 0xffu) - 127 + 15;
    if (exponent <= 0) return static_cast<unsigned short>(sign);
    if (exponent >= 31) return static_cast<unsigned short>(sign | 0x7c00u);
    return static_cast<unsigned short>(sign | (static_cast<std::uint32_t>(exponent) << 10) |
                                       ((bits & 0x7fffffu) >> 13));
}

/* The quantized product is checked with one case per launch shape the host
   picks: a short-k fallback, a wide prefill, an m == 1 step that splits the
   reduction across eight lanes, and the fp16-scale variant. */
void run_nbits_case(const char* name, int m, int k, int n, int block_size, bool f16_scales) {
    const int bits = 4;
    const int blocks = (k + block_size - 1) / block_size;
    std::vector<float> a = random_values(static_cast<std::size_t>(m) * k, 3u);
    std::vector<float> scale_values(static_cast<std::size_t>(n) * blocks);
    for (std::size_t index = 0; index < scale_values.size(); ++index) {
        scale_values[index] = 0.25f + 0.5f * static_cast<float>(index % 3);
    }
    std::vector<unsigned short> scales_f16(scale_values.size());
    for (std::size_t index = 0; index < scale_values.size(); ++index) {
        scales_f16[index] = to_half_bits(scale_values[index]);
    }
    std::vector<unsigned char> packed(static_cast<std::size_t>(n) * blocks * (block_size * bits / 8));
    unsigned state = 7u;
    for (std::size_t index = 0; index < packed.size(); ++index) {
        state = state * 1103515245u + 12345u;
        packed[index] = static_cast<unsigned char>((state >> 16) & 0xff);
    }
    std::vector<float> expected(static_cast<std::size_t>(m) * n, 0.0f);
    for (int row = 0; row < m; ++row) {
        for (int col = 0; col < n; ++col) {
            /* Accumulated in double: this is the value the kernel is asked to
               reproduce, and the reference's own rounding error over a
               thousand terms is the same size as the difference the check is
               looking for. */
            double sum = 0.0;
            for (int index = 0; index < k; ++index) {
                int block = index / block_size;
                int offset = index - block * block_size;
                int packed_index = (col * blocks + block) * (block_size * bits / 8) +
                                   (offset / (8 / bits));
                unsigned char value = packed[packed_index];
                int nibble = (offset & 1) ? (value >> 4) : (value & 0x0f);
                sum += static_cast<double>(a[static_cast<std::size_t>(row) * k + index]) *
                       static_cast<double>(nibble - 8) *
                       static_cast<double>(scale_values[col * blocks + block]);
            }
            expected[static_cast<std::size_t>(row) * n + col] = static_cast<float>(sum);
        }
    }
    std::vector<float> actual(static_cast<std::size_t>(m) * n, 0.0f);
    const int status = f16_scales
        ? fsv_cuda_matmul_nbits_f16scales_host(a.data(), packed.data(), scales_f16.data(),
                                               actual.data(), m, k, n, block_size, bits)
        : fsv_cuda_matmul_nbits_f32_host(a.data(), packed.data(), scale_values.data(),
                                         actual.data(), m, k, n, block_size, bits);
    /* Splitting the reduction reorders the sum, so the tolerance has to scale
       with the magnitude of the result rather than sit at a fixed 1e-4. */
    const double worst = max_abs_error(expected, actual);
    double peak = 0.0;
    for (float value : expected) peak = std::fmax(peak, std::fabs(value));
    const double limit = 1e-6 * (peak > 1.0 ? peak : 1.0);
    report(name, status == 0 && worst <= limit,
           status == 0 ? "max err " + std::to_string(worst) + " limit " +
                             std::to_string(limit)
                       : error_detail());
}

void check_matmul_nbits() {
    run_nbits_case("matmul_nbits_f32", 3, 5, 4, 4, false);
    run_nbits_case("matmul_nbits_prefill", 4, 512, 64, 128, false);
    run_nbits_case("matmul_nbits_step", 1, 256, 8, 128, false);
    run_nbits_case("matmul_nbits_f16scales", 2, 64, 16, 32, true);
    /* A slice length that is not a multiple of the vector group: the host
       rounds it up, so the last lanes start past the end of the reduction. */
    run_nbits_case("matmul_nbits_lane_pad", 16, 1536, 64, 128, false);
}

void check_elementwise() {
    const std::size_t count = 257;
    std::vector<float> a = random_values(count, 5u);
    std::vector<float> b = random_values(count, 6u);
    std::vector<float> expected(count);
    for (std::size_t index = 0; index < count; ++index) expected[index] = a[index] + b[index];
    std::vector<float> actual(count, 0.0f);
    int status = fsv_cuda_binary_f32_host(a.data(), b.data(), actual.data(), count, 0);
    report("binary_add", status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "ok" : error_detail());

    for (std::size_t index = 0; index < count; ++index) expected[index] = a[index] > 0.0f ? a[index] : 0.0f;
    status = fsv_cuda_unary_f32_host(a.data(), actual.data(), count, 0);
    report("unary_relu", status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "ok" : error_detail());
}

void check_normalization() {
    const int rows = 3;
    const int width = 9;
    std::vector<float> input = random_values(static_cast<std::size_t>(rows) * width, 11u);
    std::vector<float> scale = random_values(width, 12u);
    std::vector<float> bias = random_values(width, 13u);
    std::vector<float> expected(input.size(), 0.0f);
    for (int row = 0; row < rows; ++row) {
        const float* row_input = input.data() + static_cast<std::size_t>(row) * width;
        float mean = 0.0f;
        for (int i = 0; i < width; ++i) mean += row_input[i];
        mean /= width;
        float variance = 0.0f;
        for (int i = 0; i < width; ++i) variance += (row_input[i] - mean) * (row_input[i] - mean);
        float inverse = 1.0f / std::sqrt(variance / width + 1e-5f);
        for (int i = 0; i < width; ++i) {
            expected[static_cast<std::size_t>(row) * width + i] =
                (row_input[i] - mean) * inverse * scale[i] + bias[i];
        }
    }
    std::vector<float> actual(input.size(), 0.0f);
    int status = fsv_cuda_layernorm_f32_host(input.data(), scale.data(), bias.data(),
                                             actual.data(), rows, width, 1e-5f);
    report("layernorm", status == 0 && close_enough(expected, actual, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());

    for (int row = 0; row < rows; ++row) {
        const float* row_input = input.data() + static_cast<std::size_t>(row) * width;
        float maximum = row_input[0];
        for (int i = 1; i < width; ++i) maximum = std::fmax(maximum, row_input[i]);
        float total = 0.0f;
        for (int i = 0; i < width; ++i) total += std::exp(row_input[i] - maximum);
        for (int i = 0; i < width; ++i) {
            expected[static_cast<std::size_t>(row) * width + i] =
                std::exp(row_input[i] - maximum) / total;
        }
    }
    status = fsv_cuda_softmax_f32_host(input.data(), actual.data(), rows, width);
    report("softmax", status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* The graph runtime takes the four-channels-per-thread kernel whenever the
   output channels divide into blocks of four, so both kernels are checked
   here, over a plain case, a padded strided one and a depthwise one. */
void run_conv2d_case(const char* name, int batch, int channels, int height, int width,
                     int out_channels, int kernel_h, int kernel_w, int stride, int pad,
                     int dilation, int groups) {
    const int out_h = (height + 2 * pad - dilation * (kernel_h - 1) - 1) / stride + 1;
    const int out_w = (width + 2 * pad - dilation * (kernel_w - 1) - 1) / stride + 1;
    const int in_per_group = channels / groups;
    const int out_per_group = out_channels / groups;
    std::vector<float> input =
        random_values(static_cast<std::size_t>(batch) * channels * height * width, 21u);
    std::vector<float> weight = random_values(
        static_cast<std::size_t>(out_channels) * in_per_group * kernel_h * kernel_w, 22u);
    std::vector<float> bias = random_values(static_cast<std::size_t>(out_channels), 23u);
    std::vector<float> expected(
        static_cast<std::size_t>(batch) * out_channels * out_h * out_w, 0.0f);
    for (int nb = 0; nb < batch; ++nb) {
        for (int oc = 0; oc < out_channels; ++oc) {
            const int group = oc / out_per_group;
            for (int oy = 0; oy < out_h; ++oy) {
                for (int ox = 0; ox < out_w; ++ox) {
                    float sum = bias[static_cast<std::size_t>(oc)];
                    for (int ic = 0; ic < in_per_group; ++ic) {
                        for (int ky = 0; ky < kernel_h; ++ky) {
                            const int iy = oy * stride - pad + ky * dilation;
                            if (iy < 0 || iy >= height) continue;
                            for (int kx = 0; kx < kernel_w; ++kx) {
                                const int ix = ox * stride - pad + kx * dilation;
                                if (ix < 0 || ix >= width) continue;
                                const std::size_t input_index =
                                    ((static_cast<std::size_t>(nb) * channels +
                                      group * in_per_group + ic) * height + iy) * width + ix;
                                const std::size_t weight_index =
                                    ((static_cast<std::size_t>(oc) * in_per_group + ic) * kernel_h +
                                     ky) * kernel_w + kx;
                                sum += input[input_index] * weight[weight_index];
                            }
                        }
                    }
                    expected[((static_cast<std::size_t>(nb) * out_channels + oc) * out_h + oy) *
                                 out_w + ox] = sum;
                }
            }
        }
    }
    std::vector<float> actual(expected.size(), 0.0f);
    const int status = fsv_cuda_conv2d_f32_host(
        input.data(), weight.data(), bias.data(), actual.data(), batch, channels, height,
        width, out_channels, kernel_h, kernel_w, pad, pad, pad, pad, stride, stride, dilation,
        dilation, groups);
    report(name, status == 0 && close_enough(expected, actual, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

void check_conv() {
    run_conv2d_case("conv2d", 1, 1, 3, 3, 1, 2, 2, 1, 0, 1, 1);
    run_conv2d_case("conv2d_tiled", 1, 2, 4, 5, 8, 2, 2, 1, 0, 1, 1);
    run_conv2d_case("conv2d_tiled_stride", 2, 4, 5, 6, 8, 3, 3, 2, 1, 1, 1);
    run_conv2d_case("conv2d_depthwise", 1, 8, 4, 4, 8, 3, 3, 1, 1, 1, 8);
    /* 1x1 convolutions are the linear layers the wider channel tiles exist
       for, with and without groups. */
    run_conv2d_case("conv2d_tiled16", 1, 3, 4, 5, 16, 1, 1, 1, 0, 1, 1);
    run_conv2d_case("conv2d_tiled16_group", 1, 4, 3, 3, 32, 1, 1, 1, 0, 1, 2);

    /* ConvTranspose1D: one input channel, one output channel, kernel 3. */
    const int in_width = 4;
    const int stride = 2;
    const int pad_left = 1;
    const int kernel = 3;
    const int out_width = (in_width - 1) * stride - pad_left + kernel;
    std::vector<float> signal = random_values(in_width, 31u);
    std::vector<float> taps = random_values(static_cast<std::size_t>(kernel), 32u);
    std::vector<float> transposed(static_cast<std::size_t>(out_width), 0.0f);
    for (int ox = 0; ox < out_width; ++ox) {
        float sum = 0.0f;
        for (int k = 0; k < kernel; ++k) {
            int numerator = ox + pad_left - k;
            if (numerator < 0 || numerator % stride != 0) continue;
            int ix = numerator / stride;
            if (ix < 0 || ix >= in_width) continue;
            sum += signal[ix] * taps[k];
        }
        transposed[ox] = sum;
    }
    std::vector<float> actual_transposed(static_cast<std::size_t>(out_width), 0.0f);
    const int status = fsv_cuda_conv_transpose1d_f32_host(
        signal.data(), taps.data(), nullptr, actual_transposed.data(), 1, 1, in_width, 1,
        kernel, pad_left, 0, stride, 1, 1, 0);
    report("conv_transpose1d", status == 0 && close_enough(transposed, actual_transposed, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(transposed, actual_transposed))
                       : error_detail());
}

/* ---------------------------------------------------------------------
   Device-path coverage.

   Everything above calls a *_host helper, which uploads, runs and downloads
   inside one call. The graph runtime instead keeps tensors resident and calls
   the *_dev entries with an index table, and that is the path a synthesis
   takes: the batched product and the index it consumes are checked here
   against a reference that follows the broadcast rules directly. The index is
   built by the same rule the runtime uses, so a mistake there would still be
   caught by the end-to-end parity check against ONNX Runtime, not here.
   --------------------------------------------------------------------- */

std::vector<long long> broadcast_batch(const std::vector<long long>& left,
                                      const std::vector<long long>& right) {
    const std::size_t rank = left.size() > right.size() ? left.size() : right.size();
    std::vector<long long> output(rank, 1);
    for (std::size_t index = 0; index < rank; ++index) {
        const long long a = index < left.size() ? left[left.size() - 1 - index] : 1;
        const long long b = index < right.size() ? right[right.size() - 1 - index] : 1;
        if (a != b && a != 1 && b != 1) return {};
        output[rank - 1 - index] = a > b ? a : b;
    }
    return output;
}

std::vector<long long> element_strides(const std::vector<long long>& shape) {
    std::vector<long long> strides(shape.size(), 1);
    for (std::size_t index = shape.size(); index > 1; --index) {
        strides[index - 2] = strides[index - 1] * shape[index - 1];
    }
    return strides;
}

long long shape_numel(const std::vector<long long>& shape) {
    long long total = 1;
    for (long long dim : shape) total *= dim;
    return total;
}

/* Row-major offset of one batch slice, as the product of the coordinates with
   the strides of each operand's own batch shape. */
long long batch_offset(const std::vector<long long>& batch,
                       const std::vector<long long>& operand_batch,
                       const std::vector<long long>& operand_strides,
                       long long slice, long long matrix) {
    long long offset = 0;
    long long remaining = slice;
    const long long rank = static_cast<long long>(batch.size());
    for (long long dim = rank - 1; dim >= 0; --dim) {
        const long long size = batch[static_cast<std::size_t>(dim)];
        const long long coordinate = size ? remaining % size : 0;
        remaining = size ? remaining / size : 0;
        const long long operand_dim = dim - (rank - static_cast<long long>(operand_batch.size()));
        if (operand_dim >= 0 && operand_batch[static_cast<std::size_t>(operand_dim)] != 1) {
            offset += coordinate * operand_strides[static_cast<std::size_t>(operand_dim)] * matrix;
        }
    }
    return offset;
}

std::vector<float> batched_matmul_reference(const std::vector<float>& left,
                                           const std::vector<long long>& left_batch,
                                           const std::vector<float>& right,
                                           const std::vector<long long>& right_batch,
                                           long long m, long long k, long long n) {
    const std::vector<long long> batch = broadcast_batch(left_batch, right_batch);
    const std::vector<long long> left_strides = element_strides(left_batch);
    const std::vector<long long> right_strides = element_strides(right_batch);
    const long long slices = shape_numel(batch);
    std::vector<float> output(static_cast<std::size_t>(slices * m * n), 0.0f);
    for (long long slice = 0; slice < slices; ++slice) {
        const long long left_base = batch_offset(batch, left_batch, left_strides, slice, m * k);
        const long long right_base = batch_offset(batch, right_batch, right_strides, slice, k * n);
        for (long long row = 0; row < m; ++row) {
            for (long long col = 0; col < n; ++col) {
                float sum = 0.0f;
                for (long long index = 0; index < k; ++index) {
                    sum += left[static_cast<std::size_t>(left_base + row * k + index)] *
                           right[static_cast<std::size_t>(right_base + index * n + col)];
                }
                output[static_cast<std::size_t>((slice * m + row) * n + col)] = sum;
            }
        }
    }
    return output;
}

bool run_matmul_batch_case(const char* name, const std::vector<long long>& left_batch,
                           const std::vector<long long>& right_batch, long long m, long long k,
                           long long n, bool plain_matrix_path) {
    const std::vector<long long> batch = broadcast_batch(left_batch, right_batch);
    const long long slices = shape_numel(batch);
    const std::vector<float> left =
        random_values(static_cast<std::size_t>(shape_numel(left_batch) * m * k), 11u);
    const std::vector<float> right =
        random_values(static_cast<std::size_t>(shape_numel(right_batch) * k * n), 12u);
    const std::vector<float> expected =
        batched_matmul_reference(left, left_batch, right, right_batch, m, k, n);

    fsv_cuda_index index{};
    index.rank = static_cast<int>(batch.size());
    const std::vector<long long> left_strides = element_strides(left_batch);
    const std::vector<long long> right_strides = element_strides(right_batch);
    const long long rank = static_cast<long long>(batch.size());
    for (long long dim = 0; dim < rank; ++dim) {
        index.shape[dim] = batch[static_cast<std::size_t>(dim)];
        const long long left_dim = dim - (rank - static_cast<long long>(left_batch.size()));
        const long long right_dim = dim - (rank - static_cast<long long>(right_batch.size()));
        index.a_stride[dim] =
            left_dim >= 0 && left_batch[static_cast<std::size_t>(left_dim)] != 1
                ? left_strides[static_cast<std::size_t>(left_dim)] * m * k : 0;
        index.b_stride[dim] =
            right_dim >= 0 && right_batch[static_cast<std::size_t>(right_dim)] != 1
                ? right_strides[static_cast<std::size_t>(right_dim)] * k * n : 0;
    }

    fsv_cuda_ptr left_device = 0;
    fsv_cuda_ptr right_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t left_bytes = left.size() * sizeof(float);
    const std::size_t right_bytes = right.size() * sizeof(float);
    const std::size_t output_bytes = expected.size() * sizeof(float);
    bool ok = fsv_cuda_device_alloc(left_bytes, &left_device) == 0 &&
              fsv_cuda_device_alloc(right_bytes, &right_device) == 0 &&
              fsv_cuda_device_alloc(output_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(left_device, left.data(), left_bytes) == 0 &&
              fsv_cuda_device_upload(right_device, right.data(), right_bytes) == 0;
    int status = -1;
    if (ok) {
        status = plain_matrix_path
            ? fsv_cuda_matmul_dev(left_device, right_device, output_device,
                                  static_cast<int>(m), static_cast<int>(k),
                                  static_cast<int>(n))
            : fsv_cuda_matmul_f32_batched(left_device, right_device, output_device,
                                          static_cast<int>(m), static_cast<int>(k),
                                          static_cast<int>(n), slices, 0, &index);
    }
    std::vector<float> actual(expected.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, output_bytes) == 0;
    }
    if (left_device) fsv_cuda_device_free(left_device, left_bytes);
    if (right_device) fsv_cuda_device_free(right_device, right_bytes);
    if (output_device) fsv_cuda_device_free(output_device, output_bytes);

    const bool passed = ok && status == 0 && close_enough(expected, actual, 1e-4);
    report(name, passed, status == 0
                            ? "max err " + std::to_string(max_abs_error(expected, actual))
                            : error_detail());
    return passed;
}

void check_matmul_device_paths() {
    run_matmul_batch_case("matmul_dev_plain", {}, {}, 3, 4, 2, true);
    run_matmul_batch_case("matmul_batched_1d", {4}, {4}, 3, 5, 2, false);
    run_matmul_batch_case("matmul_batched_bcast", {2, 1}, {1, 4}, 3, 5, 2, false);
    run_matmul_batch_case("matmul_batched_vec", {}, {2}, 1, 5, 4, false);
    run_matmul_batch_case("matmul_batched_rank2", {2, 3}, {2, 3}, 2, 3, 2, false);
    /* The cached decoder step has m == 1, so the reduction is split over a
       lane group and the activation row is read four values at a time; these
       are the shapes that reach those paths. */
    run_matmul_batch_case("matmul_batched_split_k", {}, {}, 1, 128, 64, false);
    run_matmul_batch_case("matmul_batched_vector", {}, {}, 2, 128, 8, false);
    run_matmul_batch_case("matmul_batched_split_batch", {2, 3}, {2, 3}, 2, 64, 4, false);
}

float apply_reference(int operation, float x, float y) {
    switch (operation) {
        case 0: return x + y;
        case 1: return x * y;
        case 2: return x - y;
        case 3: return x / y;
        case 5: return std::fmax(x, y);
        case 6: return std::fmin(x, y);
        default: return x;
    }
}

/* One broadcast operand is a single value. The graph runtime takes this path
   for every scalar fused into an activation, and a wrong stride table would
   quietly reuse the first element. */
void run_binary_scalar_case(const char* name, int operation, bool scalar_first) {
    const int rows = 4;
    const int columns = 3;
    const float factor = 2.5f;
    std::vector<float> a = random_values(static_cast<std::size_t>(rows) * columns, 21u);
    std::vector<float> expected(a.size(), 0.0f);
    for (std::size_t index = 0; index < a.size(); ++index) {
        expected[index] = scalar_first ? apply_reference(operation, factor, a[index])
                                       : apply_reference(operation, a[index], factor);
    }

    fsv_cuda_ptr a_device = 0;
    fsv_cuda_ptr scalar_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t bytes = a.size() * sizeof(float);
    bool ok = fsv_cuda_device_alloc(bytes, &a_device) == 0 &&
              fsv_cuda_device_alloc(sizeof(float), &scalar_device) == 0 &&
              fsv_cuda_device_alloc(bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(a_device, a.data(), bytes) == 0 &&
              fsv_cuda_device_upload(scalar_device, &factor, sizeof(float)) == 0;
    fsv_cuda_index index{};
    index.rank = 2;
    index.shape[0] = rows;
    index.shape[1] = columns;
    index.operation = operation;
    /* The strides follow the operand order handed to the entry point, so the
       scalar always carries the all-zero row. */
    index.a_stride[0] = scalar_first ? 0 : columns;
    index.a_stride[1] = scalar_first ? 0 : 1;
    index.b_stride[0] = scalar_first ? columns : 0;
    index.b_stride[1] = scalar_first ? 1 : 0;
    int status = -1;
    if (ok) {
        status = fsv_cuda_binary_bcast_f32(scalar_first ? scalar_device : a_device,
                                           scalar_first ? a_device : scalar_device,
                                           output_device, a.size(), &index);
    }
    std::vector<float> actual(a.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, bytes) == 0;
    }
    if (a_device) fsv_cuda_device_free(a_device, bytes);
    if (scalar_device) fsv_cuda_device_free(scalar_device, sizeof(float));
    if (output_device) fsv_cuda_device_free(output_device, bytes);
    report(name, ok && status == 0 && close_enough(expected, actual, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* out = source * factor, the shape Gemm takes for alpha. */
void run_scale_case() {
    const std::size_t count = 300;
    const float factor = 0.25f;
    std::vector<float> a = random_values(count, 41u);
    std::vector<float> expected(count);
    for (std::size_t index = 0; index < count; ++index) expected[index] = a[index] * factor;
    fsv_cuda_ptr a_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t bytes = count * sizeof(float);
    bool ok = fsv_cuda_device_alloc(bytes, &a_device) == 0 &&
              fsv_cuda_device_alloc(bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(a_device, a.data(), bytes) == 0;
    int status = -1;
    if (ok) status = fsv_cuda_scale_into_f32(a_device, output_device, count, factor);
    std::vector<float> actual(count, 0.0f);
    if (ok && status == 0) ok = fsv_cuda_device_download(actual.data(), output_device, bytes) == 0;
    if (a_device) fsv_cuda_device_free(a_device, bytes);
    if (output_device) fsv_cuda_device_free(output_device, bytes);
    report("scale_into_dev", ok && status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* The [outer][mid][inner] block reduction behind ReduceSum and friends. */
void run_reduce_case(const char* name, long long outer, long long mid, long long inner,
                     int operation) {
    const std::size_t count = static_cast<std::size_t>(outer * mid * inner);
    std::vector<float> input = random_values(count, 51u + static_cast<unsigned>(operation));
    const std::size_t out_count = static_cast<std::size_t>(outer * inner);
    std::vector<float> expected(out_count, 0.0f);
    for (std::size_t slot = 0; slot < out_count; ++slot) {
        const long long row = static_cast<long long>(slot) / inner;
        const long long column = static_cast<long long>(slot) % inner;
        double accumulator = 0.0;
        double best = -1.0e30;
        for (long long step = 0; step < mid; ++step) {
            const float value = input[static_cast<std::size_t>((row * mid + step) * inner + column)];
            if (operation == 2) best = std::fmax(best, value);
            else accumulator += operation == 3 ? static_cast<double>(value) * value : value;
        }
        if (operation == 1) accumulator /= static_cast<double>(mid);
        else if (operation == 2) accumulator = best;
        else if (operation == 3) accumulator = std::sqrt(accumulator);
        expected[slot] = static_cast<float>(accumulator);
    }
    fsv_cuda_ptr input_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t input_bytes = count * sizeof(float);
    const std::size_t output_bytes = out_count * sizeof(float);
    bool ok = fsv_cuda_device_alloc(input_bytes, &input_device) == 0 &&
              fsv_cuda_device_alloc(output_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(input_device, input.data(), input_bytes) == 0;
    int status = -1;
    if (ok) {
        status = fsv_cuda_reduce_block_f32(input_device, output_device, outer, mid, inner,
                                           operation);
    }
    std::vector<float> actual(out_count, 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, output_bytes) == 0;
    }
    if (input_device) fsv_cuda_device_free(input_device, input_bytes);
    if (output_device) fsv_cuda_device_free(output_device, output_bytes);
    report(name, ok && status == 0 && close_enough(expected, actual, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* GatherElements keeps the index shape and substitutes one coordinate. */
void run_gather_elements_case() {
    const int rows = 5;
    const int columns = 4;
    std::vector<float> data(static_cast<std::size_t>(rows) * columns);
    for (std::size_t index = 0; index < data.size(); ++index) {
        data[index] = static_cast<float>(index) * 0.5f;
    }
    const long long raw_indices[rows * columns] = {3, 1, 4, 0, 0, 2, 2, 3, 4, 4, 1, 1,
                                                   2, 0, 3, 2, 1, 3, 0, 4};
    std::vector<float> expected(data.size(), 0.0f);
    for (int row = 0; row < rows; ++row) {
        for (int column = 0; column < columns; ++column) {
            const std::size_t slot = static_cast<std::size_t>(row) * columns + column;
            expected[slot] = data[static_cast<std::size_t>(raw_indices[slot]) * columns + column];
        }
    }
    fsv_cuda_ptr data_device = 0;
    fsv_cuda_ptr indices_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t data_bytes = data.size() * sizeof(float);
    const std::size_t index_bytes = expected.size() * sizeof(long long);
    bool ok = fsv_cuda_device_alloc(data_bytes, &data_device) == 0 &&
              fsv_cuda_device_alloc(index_bytes, &indices_device) == 0 &&
              fsv_cuda_device_alloc(data_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(data_device, data.data(), data_bytes) == 0 &&
              fsv_cuda_device_upload(indices_device, raw_indices, index_bytes) == 0;
    fsv_cuda_index index{};
    index.rank = 2;
    index.shape[0] = rows;
    index.shape[1] = columns;
    index.a_stride[0] = columns;
    index.a_stride[1] = 1;
    int status = -1;
    if (ok) {
        status = fsv_cuda_gather_elements_f32(data_device, indices_device, output_device,
                                              expected.size(), 0, rows, &index);
    }
    std::vector<float> actual(expected.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, data_bytes) == 0;
    }
    if (data_device) fsv_cuda_device_free(data_device, data_bytes);
    if (indices_device) fsv_cuda_device_free(indices_device, index_bytes);
    if (output_device) fsv_cuda_device_free(output_device, data_bytes);
    report("gather_elements_dev", ok && status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* ScatterElements(reduction="none"): the result starts as a copy of the
   operand and the updates overwrite the positions the indices select. The
   index tensor is narrower than the operand along the scattered axis and one
   index is negative, which is the wrap the decoder's shapes rely on. */
void run_scatter_elements_case() {
    const int rows = 5;
    const int columns = 6;
    const int placed = 3;
    std::vector<float> data(static_cast<std::size_t>(rows) * columns);
    for (std::size_t index = 0; index < data.size(); ++index) {
        data[index] = static_cast<float>(index) * 0.25f;
    }
    /* Indices are distinct within a row: reduction="none" leaves the order of
       two writes to the same slot undefined, so a duplicate could not be
       asserted against a reference the way the rest of this case is. */
    const long long raw_indices[rows * placed] = {4, 1, 5, 0, 2, -1, 3, 5, 1,
                                                  2, 0, 4, 3, 0, 4};
    std::vector<float> updates(static_cast<std::size_t>(rows) * placed);
    for (std::size_t index = 0; index < updates.size(); ++index) {
        updates[index] = 100.0f + static_cast<float>(index);
    }
    std::vector<float> expected = data;
    for (int row = 0; row < rows; ++row) {
        for (int column = 0; column < placed; ++column) {
            const std::size_t slot = static_cast<std::size_t>(row) * placed + column;
            long long target = raw_indices[slot];
            if (target < 0) target += columns;
            expected[static_cast<std::size_t>(row) * columns +
                     static_cast<std::size_t>(target)] = updates[slot];
        }
    }
    fsv_cuda_ptr output_device = 0;
    fsv_cuda_ptr indices_device = 0;
    fsv_cuda_ptr updates_device = 0;
    const std::size_t data_bytes = data.size() * sizeof(float);
    const std::size_t index_bytes = updates.size() * sizeof(long long);
    const std::size_t update_bytes = updates.size() * sizeof(float);
    /* The entry point takes the operand already copied into the destination,
       which is what the graph's device path does with one box copy first. */
    bool ok = fsv_cuda_device_alloc(data_bytes, &output_device) == 0 &&
              fsv_cuda_device_alloc(index_bytes, &indices_device) == 0 &&
              fsv_cuda_device_alloc(update_bytes, &updates_device) == 0 &&
              fsv_cuda_device_upload(output_device, data.data(), data_bytes) == 0 &&
              fsv_cuda_device_upload(indices_device, raw_indices, index_bytes) == 0 &&
              fsv_cuda_device_upload(updates_device, updates.data(), update_bytes) == 0;
    fsv_cuda_index index{};
    index.rank = 2;
    index.shape[0] = rows;
    index.shape[1] = placed;
    index.a_stride[0] = columns;
    index.a_stride[1] = 1;
    int status = -1;
    if (ok) {
        status = fsv_cuda_scatter_elements_f32(updates_device, indices_device, output_device,
                                               updates.size(), 1, columns, &index);
    }
    std::vector<float> actual(data.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, data_bytes) == 0;
    }
    if (output_device) fsv_cuda_device_free(output_device, data_bytes);
    if (indices_device) fsv_cuda_device_free(indices_device, index_bytes);
    if (updates_device) fsv_cuda_device_free(updates_device, update_bytes);
    report("scatter_elements_dev", ok && status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* ArgMax keeps the lowest index when two values tie, which is what the host
   reference does and what the decode loop's token pick relies on. */
void run_argmax_case() {
    const int outer = 3;
    const int mid = 4;
    const int inner = 2;
    std::vector<float> input(static_cast<std::size_t>(outer) * mid * inner);
    for (std::size_t index = 0; index < input.size(); ++index) {
        input[index] = static_cast<float>(index % 7) * 0.25f;
    }
    /* A pair of equal maxima: the first one has to win. */
    input[static_cast<std::size_t>((1 * mid + 2) * inner + 0)] = 9.0f;
    input[static_cast<std::size_t>((1 * mid + 3) * inner + 0)] = 9.0f;
    input[static_cast<std::size_t>((2 * mid + 1) * inner + 1)] = 5.0f;
    std::vector<long long> expected(static_cast<std::size_t>(outer) * inner, 0);
    for (int o = 0; o < outer; ++o) {
        for (int in = 0; in < inner; ++in) {
            float best = -std::numeric_limits<float>::infinity();
            long long best_index = 0;
            for (int step = 0; step < mid; ++step) {
                const float value =
                    input[static_cast<std::size_t>((o * mid + step) * inner + in)];
                if (value > best) {
                    best = value;
                    best_index = step;
                }
            }
            expected[static_cast<std::size_t>(o) * inner + in] = best_index;
        }
    }
    fsv_cuda_ptr input_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t input_bytes = input.size() * sizeof(float);
    const std::size_t output_bytes = expected.size() * sizeof(long long);
    bool ok = fsv_cuda_device_alloc(input_bytes, &input_device) == 0 &&
              fsv_cuda_device_alloc(output_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(input_device, input.data(), input_bytes) == 0;
    int status = -1;
    if (ok) status = fsv_cuda_argmax_i64(input_device, output_device, outer, mid, inner);
    std::vector<long long> actual(expected.size(), -1);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, output_bytes) == 0;
    }
    if (input_device) fsv_cuda_device_free(input_device, input_bytes);
    if (output_device) fsv_cuda_device_free(output_device, output_bytes);
    bool same = ok && status == 0 && actual.size() == expected.size();
    for (std::size_t index = 0; same && index < expected.size(); ++index) {
        same = actual[index] == expected[index];
    }
    report("argmax_dev", same,
           status == 0 ? (same ? "indices match" : "index mismatch") : error_detail());
}

/* InstanceNormalization folds each (batch, channel) row over the trailing
   dimensions and then applies the per-channel scale and bias. */
void run_instance_norm_case() {
    const int batch = 1;
    const int channels = 2;
    const int spatial = 5;
    const float epsilon = 1e-5f;
    std::vector<float> input = {0.5f, -1.0f, 2.0f, 0.25f, 3.0f,
                                1.5f, 0.0f, -2.5f, 0.75f, 1.0f};
    std::vector<float> scale = {2.0f, 0.5f};
    std::vector<float> bias = {0.5f, -0.25f};
    std::vector<float> expected(input.size(), 0.0f);
    for (int channel = 0; channel < channels; ++channel) {
        double mean = 0.0;
        double square = 0.0;
        for (int step = 0; step < spatial; ++step) {
            const double value = input[static_cast<std::size_t>(channel * spatial + step)];
            mean += value;
            square += value * value;
        }
        mean /= spatial;
        const double variance = square / spatial - mean * mean;
        const double inverse = 1.0 / std::sqrt(variance + epsilon);
        for (int step = 0; step < spatial; ++step) {
            const std::size_t slot = static_cast<std::size_t>(channel * spatial + step);
            expected[slot] = static_cast<float>((input[slot] - mean) * inverse * scale[
                static_cast<std::size_t>(channel)] + bias[static_cast<std::size_t>(channel)]);
        }
    }
    fsv_cuda_ptr input_device = 0;
    fsv_cuda_ptr scale_device = 0;
    fsv_cuda_ptr bias_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t input_bytes = input.size() * sizeof(float);
    const std::size_t channel_bytes = static_cast<std::size_t>(channels) * sizeof(float);
    bool ok = fsv_cuda_device_alloc(input_bytes, &input_device) == 0 &&
              fsv_cuda_device_alloc(channel_bytes, &scale_device) == 0 &&
              fsv_cuda_device_alloc(channel_bytes, &bias_device) == 0 &&
              fsv_cuda_device_alloc(input_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(input_device, input.data(), input_bytes) == 0 &&
              fsv_cuda_device_upload(scale_device, scale.data(), channel_bytes) == 0 &&
              fsv_cuda_device_upload(bias_device, bias.data(), channel_bytes) == 0;
    int status = -1;
    if (ok) {
        status = fsv_cuda_instance_norm_f32(input_device, scale_device, bias_device,
                                           output_device, batch * channels, channels,
                                           spatial, epsilon);
    }
    std::vector<float> actual(expected.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, input_bytes) == 0;
    }
    if (input_device) fsv_cuda_device_free(input_device, input_bytes);
    if (scale_device) fsv_cuda_device_free(scale_device, channel_bytes);
    if (bias_device) fsv_cuda_device_free(bias_device, channel_bytes);
    if (output_device) fsv_cuda_device_free(output_device, input_bytes);
    report("instance_norm_dev", ok && status == 0 && close_enough(expected, actual, 1e-5),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* Pad in constant mode is the fill plus one strided copy the graph runtime now
   issues, so this covers both entries the fast path depends on. */
void run_pad_case() {
    const int rows = 2;
    const int columns = 3;
    std::vector<float> source(static_cast<std::size_t>(rows) * columns);
    for (std::size_t index = 0; index < source.size(); ++index) {
        source[index] = static_cast<float>(index) + 0.5f;
    }
    const long long begin_rows = 1;
    const long long begin_columns = 1;
    const int out_rows = rows + static_cast<int>(begin_rows);
    const int out_columns = columns + static_cast<int>(begin_columns + 2);
    std::vector<float> expected(static_cast<std::size_t>(out_rows) * out_columns, 0.0f);
    for (int row = 0; row < rows; ++row) {
        for (int column = 0; column < columns; ++column) {
            expected[static_cast<std::size_t>(row + begin_rows) * out_columns +
                     static_cast<std::size_t>(column + begin_columns)] =
                source[static_cast<std::size_t>(row) * columns + column];
        }
    }
    fsv_cuda_index index{};
    index.rank = 2;
    index.shape[0] = rows;
    index.shape[1] = columns;
    index.a_stride[0] = columns;
    index.a_stride[1] = 1;
    index.b_stride[0] = out_columns;
    index.b_stride[1] = 1;
    const long long destination_base = begin_rows * out_columns + begin_columns;
    fsv_cuda_ptr source_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t source_bytes = source.size() * sizeof(float);
    const std::size_t output_bytes = expected.size() * sizeof(float);
    bool ok = fsv_cuda_device_alloc(source_bytes, &source_device) == 0 &&
              fsv_cuda_device_alloc(output_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(source_device, source.data(), source_bytes) == 0;
    int status = -1;
    if (ok) {
        status = fsv_cuda_fill_f32(output_device, expected.size(), 0.0f);
        if (status == 0) {
            status = fsv_cuda_copy_nd_f32(source_device, output_device, source.size(), 0,
                                          destination_base, 2, &index);
        }
    }
    std::vector<float> actual(expected.size(), 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, output_bytes) == 0;
    }
    if (source_device) fsv_cuda_device_free(source_device, source_bytes);
    if (output_device) fsv_cuda_device_free(output_device, output_bytes);
    report("pad_constant_dev", ok && status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

/* Where(condition, a, b) with a byte condition. Three shapes matter: all three
   operands contiguous (the decoder's mask build), the condition broadcast over
   the result (the generic walk) and one operand a single value (the attention
   masks' fill value). */
void run_where_case(const char* name, bool condition_broadcasts, bool scalar_left,
                    bool scalar_right) {
    const int rows = 3;
    const int columns = 7;
    const std::size_t count = static_cast<std::size_t>(rows) * columns;
    std::vector<float> left = random_values(count, 71u);
    std::vector<float> right = random_values(count, 91u);
    std::vector<unsigned char> condition(count);
    for (std::size_t index = 0; index < count; ++index) {
        condition[index] = (index % 3 == 0) ? 1 : 0;
    }
    const float left_value = -2.5f;
    const float right_value = 7.25f;

    std::vector<float> expected(count);
    for (int row = 0; row < rows; ++row) {
        for (int column = 0; column < columns; ++column) {
            const std::size_t slot = static_cast<std::size_t>(row) * columns + column;
            const bool pick_left = condition_broadcasts ? (row % 3 == 0) : (slot % 3 == 0);
            expected[slot] = pick_left ? (scalar_left ? left_value : left[slot])
                                       : (scalar_right ? right_value : right[slot]);
        }
    }

    std::vector<unsigned char> condition_payload =
        condition_broadcasts ? std::vector<unsigned char>(rows) : condition;
    if (condition_broadcasts) {
        for (int row = 0; row < rows; ++row) {
            condition_payload[static_cast<std::size_t>(row)] = (row % 3 == 0) ? 1 : 0;
        }
    }

    fsv_cuda_ptr condition_device = 0;
    fsv_cuda_ptr left_device = 0;
    fsv_cuda_ptr right_device = 0;
    fsv_cuda_ptr output_device = 0;
    const std::size_t condition_bytes = condition_payload.size() * sizeof(unsigned char);
    const std::size_t left_bytes = (scalar_left ? 1 : count) * sizeof(float);
    const std::size_t right_bytes = (scalar_right ? 1 : count) * sizeof(float);
    const std::size_t output_bytes = count * sizeof(float);
    bool ok = fsv_cuda_device_alloc(condition_bytes, &condition_device) == 0 &&
              fsv_cuda_device_alloc(left_bytes, &left_device) == 0 &&
              fsv_cuda_device_alloc(right_bytes, &right_device) == 0 &&
              fsv_cuda_device_alloc(output_bytes, &output_device) == 0 &&
              fsv_cuda_device_upload(condition_device, condition_payload.data(),
                                     condition_bytes) == 0 &&
              fsv_cuda_device_upload(left_device, scalar_left ? &left_value : left.data(),
                                     left_bytes) == 0 &&
              fsv_cuda_device_upload(right_device, scalar_right ? &right_value : right.data(),
                                     right_bytes) == 0;
    fsv_cuda_where_index index{};
    index.rank = 2;
    index.shape[0] = rows;
    index.shape[1] = columns;
    index.condition_stride[0] = condition_broadcasts ? 1 : columns;
    index.condition_stride[1] = condition_broadcasts ? 0 : 1;
    index.a_stride[0] = scalar_left ? 0 : columns;
    index.a_stride[1] = scalar_left ? 0 : 1;
    index.b_stride[0] = scalar_right ? 0 : columns;
    index.b_stride[1] = scalar_right ? 0 : 1;
    int status = -1;
    if (ok) {
        status = fsv_cuda_where_f32(condition_device, left_device, right_device, output_device,
                                    count, &index);
    }
    std::vector<float> actual(count, 0.0f);
    if (ok && status == 0) {
        ok = fsv_cuda_device_download(actual.data(), output_device, output_bytes) == 0;
    }
    if (condition_device) fsv_cuda_device_free(condition_device, condition_bytes);
    if (left_device) fsv_cuda_device_free(left_device, left_bytes);
    if (right_device) fsv_cuda_device_free(right_device, right_bytes);
    if (output_device) fsv_cuda_device_free(output_device, output_bytes);
    report(name, ok && status == 0 && close_enough(expected, actual, 1e-6),
           status == 0 ? "max err " + std::to_string(max_abs_error(expected, actual))
                       : error_detail());
}

void check_device_operators() {
    run_binary_scalar_case("binary_scalar_mul", 1, false);
    run_binary_scalar_case("binary_scalar_div_first", 3, true);
    run_scale_case();
    run_reduce_case("reduce_block_sum", 4, 15, 1, 0);
    run_reduce_case("reduce_block_mean", 2, 3, 4, 1);
    run_reduce_case("reduce_block_max", 4, 15, 1, 2);
    run_reduce_case("reduce_block_l2", 3, 5, 2, 3);
    run_gather_elements_case();
    run_scatter_elements_case();
    run_argmax_case();
    run_instance_norm_case();
    run_pad_case();
    run_where_case("where_contiguous", false, false, false);
    run_where_case("where_condition_broadcast", true, false, false);
    run_where_case("where_scalar_right", false, false, true);
    run_where_case("where_scalar_left", false, true, false);
}
}  // namespace

int main() {
    std::printf("self-written CUDA voice runtime smoke test\n");
    check_device();
    check_matmul();
    check_matmul_device_paths();
    check_matmul_nbits();
    check_elementwise();
    check_normalization();
    check_conv();
    check_device_operators();
    std::printf("%s (%d failing case(s))\n", g_failures == 0 ? "ALL PASS" : "FAILED", g_failures);
    return g_failures == 0 ? 0 : 1;
}
