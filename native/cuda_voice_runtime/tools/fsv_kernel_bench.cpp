/* Dev-only micro-benchmark for the self-written CUDA kernels.

   The graph-level benchmark cannot resolve one kernel against another: a
   single synthesis issues thousands of launches across a dozen operators, so a
   change of a tenth of a millisecond per launch drowns in wall-clock noise.
   This tool times one kernel at a time through the public DLL entry points.

   Every measurement queues `iters` launches back to back and waits once at the
   end, so the number is the sustained device cost per launch rather than the
   driver's enqueue latency. It is a relative instrument: run it before and
   after a kernel change with the same shapes and it says whether the kernel
   really got faster. */

#include "fsv_cuda_voice_runtime.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

[[noreturn]] void fail(const char* what) {
    const char* text = fsv_cuda_last_error();
    std::printf("FAIL %s: %s\n", what, text && *text ? text : "unknown");
    std::exit(1);
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

fsv_cuda_ptr upload_bytes(const void* data, std::size_t bytes) {
    fsv_cuda_ptr pointer = 0;
    if (fsv_cuda_device_alloc(bytes, &pointer) != 0) fail("device_alloc");
    if (bytes && fsv_cuda_device_upload(pointer, data, bytes) != 0) fail("device_upload");
    return pointer;
}

template <typename Fn>
double time_launches(Fn&& launch, int warmup, int iters) {
    for (int index = 0; index < warmup; ++index) launch();
    if (fsv_cuda_device_sync() != 0) fail("warmup sync");
    const Clock::time_point start = Clock::now();
    for (int index = 0; index < iters; ++index) launch();
    if (fsv_cuda_device_sync() != 0) fail("sync");
    const Clock::time_point end = Clock::now();
    return std::chrono::duration<double, std::micro>(end - start).count() / iters;
}

void report(const char* label, const char* shape, double micros, double bytes, double checksum) {
    const double gigabytes = bytes / 1073741824.0;
    const double giga_bytes_per_second = gigabytes / (micros * 1e-6);
    std::printf("%-16s %-34s %9.2f us/launch  %8.1f GB/s  sum=%.4f\n", label, shape, micros,
                giga_bytes_per_second, checksum);
}

/* c[slice, m, n] = a[slice, m, k] @ b[slice, k, n] with no broadcast index. */
void bench_matmul_f32(int m, int k, int n, long long batch, int warmup, int iters) {
    const std::size_t a_count = static_cast<std::size_t>(batch) * m * k;
    const std::size_t b_count = static_cast<std::size_t>(batch) * k * n;
    const std::size_t c_count = static_cast<std::size_t>(batch) * m * n;
    std::vector<float> a = random_values(a_count, 11u);
    std::vector<float> b = random_values(b_count, 12u);
    std::vector<float> c(c_count, 0.0f);
    fsv_cuda_ptr device_a = upload_bytes(a.data(), a.size() * sizeof(float));
    fsv_cuda_ptr device_b = upload_bytes(b.data(), b.size() * sizeof(float));
    fsv_cuda_ptr device_c = upload_bytes(c.data(), c.size() * sizeof(float));
    /* Rank 0 is the contiguous case: the entry point rejects a null table. */
    fsv_cuda_index index{};
    const double micros = time_launches(
        [&] {
            if (fsv_cuda_matmul_f32_batched(device_a, device_b, device_c, m, k, n, batch, 0,
                                            &index) != 0) {
                fail("matmul_f32_batched");
            }
        },
        warmup, iters);
    if (fsv_cuda_device_download(c.data(), device_c, c.size() * sizeof(float)) != 0) {
        fail("download");
    }
    double checksum = 0.0;
    for (float value : c) checksum += value;
    /* The weights are the traffic that matters: k*n floats per slice. */
    const double bytes = static_cast<double>(b_count) * sizeof(float);
    char shape[64];
    std::snprintf(shape, sizeof(shape), "m=%d k=%d n=%d batch=%lld", m, k, n, batch);
    report("matmul_f32", shape, micros, bytes, checksum);
}

void bench_matmul_nbits4(int m, int k, int n, int block_size, int warmup, int iters) {
    const int bits = 4;
    const int blocks = (k + block_size - 1) / block_size;
    std::vector<float> a = random_values(static_cast<std::size_t>(m) * k, 21u);
    std::vector<unsigned char> packed(static_cast<std::size_t>(n) * blocks *
                                      (block_size * bits / 8), 0x21);
    std::vector<float> scales(static_cast<std::size_t>(n) * blocks, 0.25f);
    std::vector<float> c(static_cast<std::size_t>(m) * n, 0.0f);
    fsv_cuda_ptr device_a = upload_bytes(a.data(), a.size() * sizeof(float));
    fsv_cuda_ptr device_b = upload_bytes(packed.data(), packed.size());
    fsv_cuda_ptr device_s = upload_bytes(scales.data(), scales.size() * sizeof(float));
    fsv_cuda_ptr device_c = upload_bytes(c.data(), c.size() * sizeof(float));
    const double micros = time_launches(
        [&] {
            if (fsv_cuda_matmul_nbits_dev(device_a, device_b, device_s, device_c, m, k, n,
                                          block_size, bits, 0) != 0) {
                fail("matmul_nbits_dev");
            }
        },
        warmup, iters);
    if (fsv_cuda_device_download(c.data(), device_c, c.size() * sizeof(float)) != 0) {
        fail("download");
    }
    double checksum = 0.0;
    for (float value : c) checksum += value;
    const double bytes = static_cast<double>(packed.size()) + scales.size() * sizeof(float);
    char shape[64];
    std::snprintf(shape, sizeof(shape), "m=%d k=%d n=%d block=%d", m, k, n, block_size);
    report("matmul_nbits4", shape, micros, bytes, checksum);
}

void bench_conv2d(int batch, int channels, int height, int width, int out_channels,
                  int kernel_h, int kernel_w, int warmup, int iters) {
    const int out_h = height - kernel_h + 1;
    const int out_w = width - kernel_w + 1;
    std::vector<float> input = random_values(
        static_cast<std::size_t>(batch) * channels * height * width, 31u);
    std::vector<float> weight = random_values(
        static_cast<std::size_t>(out_channels) * channels * kernel_h * kernel_w, 32u);
    std::vector<float> bias = random_values(out_channels, 33u);
    std::vector<float> output(
        static_cast<std::size_t>(batch) * out_channels * out_h * out_w, 0.0f);
    fsv_cuda_ptr device_input = upload_bytes(input.data(), input.size() * sizeof(float));
    fsv_cuda_ptr device_weight = upload_bytes(weight.data(), weight.size() * sizeof(float));
    fsv_cuda_ptr device_bias = upload_bytes(bias.data(), bias.size() * sizeof(float));
    fsv_cuda_ptr device_output = upload_bytes(output.data(), output.size() * sizeof(float));
    const double micros = time_launches(
        [&] {
            if (fsv_cuda_conv2d_dev(device_input, device_weight, device_bias, device_output,
                                    batch, channels, height, width, out_channels, kernel_h,
                                    kernel_w, 0, 0, 0, 0, 1, 1, 1, 1, 1) != 0) {
                fail("conv2d_dev");
            }
        },
        warmup, iters);
    if (fsv_cuda_device_download(output.data(), device_output,
                                 output.size() * sizeof(float)) != 0) {
        fail("download");
    }
    double checksum = 0.0;
    for (float value : output) checksum += value;
    const double bytes = static_cast<double>(weight.size() + input.size()) * sizeof(float);
    char shape[80];
    std::snprintf(shape, sizeof(shape), "n=%d c=%d h=%d w=%d oc=%d k=%dx%d", batch, channels,
                  height, width, out_channels, kernel_h, kernel_w);
    report("conv2d_f32", shape, micros, bytes, checksum);
}

/* c[b, c, s] = a[b, c, s] op bias[b, c, 1]: the innermost dimension of the
   second operand is broadcast, which is the shape the graph runtime cannot
   serve with either of its contiguous kernels and therefore leaves to the
   coordinate walk. A per-channel bias over [1, C, T] is that shape. */
void bench_binary_bcast(int batch, int channels, int spatial, int warmup, int iters) {
    const std::size_t count =
        static_cast<std::size_t>(batch) * channels * spatial;
    const std::size_t bias_count = static_cast<std::size_t>(batch) * channels;
    std::vector<float> a = random_values(count, 41u);
    std::vector<float> bias = random_values(bias_count, 42u);
    std::vector<float> c(count, 0.0f);
    fsv_cuda_ptr device_a = upload_bytes(a.data(), a.size() * sizeof(float));
    fsv_cuda_ptr device_b = upload_bytes(bias.data(), bias.size() * sizeof(float));
    fsv_cuda_ptr device_c = upload_bytes(c.data(), c.size() * sizeof(float));
    fsv_cuda_index index{};
    index.rank = 3;
    index.operation = 0;
    index.shape[0] = batch;
    index.shape[1] = channels;
    index.shape[2] = spatial;
    /* a is laid out like the result; the bias advances with the channel and is
       flat along the spatial axis, so that dimension gets a zero stride. */
    index.a_stride[0] = static_cast<long long>(channels) * spatial;
    index.a_stride[1] = spatial;
    index.a_stride[2] = 1;
    index.b_stride[0] = channels;
    index.b_stride[1] = 1;
    index.b_stride[2] = 0;
    const double micros = time_launches(
        [&] {
            if (fsv_cuda_binary_bcast_f32(device_a, device_b, device_c, count, &index) != 0) {
                fail("binary_bcast_f32");
            }
        },
        warmup, iters);
    if (fsv_cuda_device_download(c.data(), device_c, c.size() * sizeof(float)) != 0) {
        fail("download");
    }
    double checksum = 0.0;
    for (float value : c) checksum += value;
    const double bytes =
        static_cast<double>(a.size() + bias.size() + c.size()) * sizeof(float);
    char shape[64];
    std::snprintf(shape, sizeof(shape), "b=%d c=%d s=%d bias-last", batch, channels, spatial);
    report("binary_bcast", shape, micros, bytes, checksum);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::printf("usage: fsv_kernel_bench <kernel> <shape...> [--iters N] [--warmup N]\n"
                    "  matmul_f32    m k n [batch]\n"
                    "  matmul_nbits4 m k n block_size\n"
                    "  conv2d        batch channels h w out_channels kh kw\n"
                    "  binary_bcast  batch channels spatial\n");
        return 2;
    }
    std::vector<int> numbers;
    int warmup = 20;
    int iters = 200;
    for (int index = 2; index < argc; ++index) {
        if (std::strcmp(argv[index], "--iters") == 0 && index + 1 < argc) {
            iters = std::atoi(argv[++index]);
        } else if (std::strcmp(argv[index], "--warmup") == 0 && index + 1 < argc) {
            warmup = std::atoi(argv[++index]);
        } else {
            numbers.push_back(std::atoi(argv[index]));
        }
    }
    const std::string kernel = argv[1];
    if (fsv_cuda_device_count() <= 0) {
        std::printf("no CUDA device\n");
        return 1;
    }
    fsv_cuda_device_info info{};
    if (fsv_cuda_get_device_info(0, &info) == 0) {
        std::printf("device: %s sm_%d%d\n", info.name, info.major, info.minor);
    }
    if (kernel == "matmul_f32") {
        if (numbers.size() < 3) return 2;
        const long long batch = numbers.size() > 3 ? numbers[3] : 1;
        bench_matmul_f32(numbers[0], numbers[1], numbers[2], batch, warmup, iters);
    } else if (kernel == "matmul_nbits4") {
        if (numbers.size() < 4) return 2;
        bench_matmul_nbits4(numbers[0], numbers[1], numbers[2], numbers[3], warmup, iters);
    } else if (kernel == "conv2d") {
        if (numbers.size() < 7) return 2;
        bench_conv2d(numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5],
                     numbers[6], warmup, iters);
    } else if (kernel == "binary_bcast") {
        if (numbers.size() < 3) return 2;
        bench_binary_bcast(numbers[0], numbers[1], numbers[2], warmup, iters);
    } else {
        std::printf("unknown kernel %s\n", kernel.c_str());
        return 2;
    }
    if (fsv_cuda_device_sync() != 0) fail("final sync");
    return 0;
}
