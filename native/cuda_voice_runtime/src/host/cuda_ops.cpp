/* Host side of the self-written CUDA kernels: same public C API as before, but
   every device call now goes through the NVIDIA driver API loaded from
   nvcuda.dll. No cudart, no cuBLAS, no toolkit on user machines. */

#include "fsv_cuda_voice_runtime.h"

#include "nv_runtime.h"

#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>

namespace {

enum { kOk = 0, kInvalidArgument = 1, kDriverFailure = 2 };

bool fail(int code) {
    return code != kOk;
}

template <typename... Args>
bool launch3(const char* name, unsigned grid_x, unsigned grid_y, unsigned grid_z,
             unsigned block_x, unsigned block_y,
            Args... arguments) {
    void* function = nullptr;
    std::string error;
    fsv::NvRuntime& runtime = fsv::NvRuntime::instance();
    if (!runtime.kernel(name, &function, error)) {
        fsv::nv_set_error(error);
        return false;
    }
    void* parameters[] = {
        const_cast<void*>(static_cast<const void*>(&arguments))...,
    };
    if (!runtime.launch(function, grid_x, grid_y, grid_z, block_x, block_y, 1, 0, parameters,
                        error)) {
        fsv::nv_set_error(error);
        return false;
    }
    return true;
}

template <typename... Args>
bool launch(const char* name, unsigned grid_x, unsigned grid_y,
            unsigned block_x, unsigned block_y,
            Args... arguments) {
    return launch3(name, grid_x, grid_y, 1, block_x, block_y, arguments...);
}

template <typename... Args>
bool launch_1d(const char* name, unsigned long long work, Args... arguments) {
    unsigned long long blocks = (work + 255ull) / 256ull;
    if (work && blocks == 0) blocks = 1;
    return launch(name, static_cast<unsigned>(blocks), 1, 256, 1, arguments...);
}

/* Weights of a voice graph are read by many nodes and never change, so the
   graph runtime registers its initializers here once. A registered buffer is
   uploaded a single time per process instead of once per node. */
struct ConstantEntry {
    std::size_t bytes = 0;
    fsv::NvPtr pointer = 0;
};

std::mutex g_constant_mutex;
std::unordered_map<const void*, ConstantEntry> g_constants;

bool constant_lookup(const void* host, std::size_t bytes, fsv::NvPtr& pointer) {
    if (!host) return false;
    std::lock_guard<std::mutex> guard(g_constant_mutex);
    auto iterator = g_constants.find(host);
    if (iterator == g_constants.end() || iterator->second.bytes != bytes) return false;
    pointer = iterator->second.pointer;
    return true;
}

/* Reinterprets a driver pointer the graph runtime owns. */
template <typename T>
const T* device_pointer(fsv_cuda_ptr pointer) {
    return reinterpret_cast<const T*>(pointer);
}

/* A device view of one host buffer. Allocations come from the runtime pool and
   go back to it when the node finishes, so a graph that runs hundreds of nodes
   does not pay for hundreds of cuMemAlloc/cuMemFree round trips. */
class DeviceBuffer {
public:
    DeviceBuffer() = default;
    ~DeviceBuffer() { reset(); }
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;

    /* Makes a host payload resident on the device. */
    bool upload(const void* host, std::size_t bytes) {
        if (!host || !bytes) return true;
        reset();
        if (constant_lookup(host, bytes, pointer_)) {
            bytes_ = bytes;
            borrowed_ = true;
            return true;
        }
        if (!reserve(bytes)) return false;
        std::string error;
        if (!fsv::NvRuntime::instance().upload(pointer_, host, bytes, error)) {
            fsv::nv_set_error(error);
            reset();
            return false;
        }
        return true;
    }

    /* Device-only scratch for an output; nothing is uploaded. */
    bool reserve(std::size_t bytes) {
        reset();
        if (!bytes) return true;
        std::string error;
        if (!fsv::NvRuntime::instance().acquire(bytes, pointer_, error)) {
            fsv::nv_set_error(error);
            pointer_ = 0;
            return false;
        }
        bytes_ = bytes;
        return true;
    }

    bool download(void* host, std::size_t bytes) const {
        if (!host || !bytes) return true;
        std::string error;
        if (!fsv::NvRuntime::instance().download(host, pointer_, bytes, error)) {
            fsv::nv_set_error(error);
            return false;
        }
        return true;
    }

    fsv::NvPtr pointer() const { return pointer_; }
    template <typename T> const T* read() const { return reinterpret_cast<const T*>(pointer_); }
    template <typename T> T* write() const { return reinterpret_cast<T*>(pointer_); }

private:
    void reset() {
        if (pointer_ && !borrowed_) fsv::NvRuntime::instance().recycle(pointer_, bytes_);
        pointer_ = 0;
        bytes_ = 0;
        borrowed_ = false;
    }

    fsv::NvPtr pointer_ = 0;
    std::size_t bytes_ = 0;
    bool borrowed_ = false;
};

/* Returns false only when the upload itself failed. */
bool to_device(const void* host, std::size_t bytes, DeviceBuffer& buffer) {
    return buffer.upload(host, bytes);
}

bool from_device(void* host, const DeviceBuffer& buffer, std::size_t bytes) {
    return buffer.download(host, bytes);
}

}  // namespace

extern "C" {

int fsv_cuda_constant_register(const void* host, std::size_t bytes) {
    if (!host || !bytes) {
        fsv::nv_set_error("invalid constant buffer");
        return kInvalidArgument;
    }
    fsv::NvPtr pointer = 0;
    if (constant_lookup(host, bytes, pointer)) return kOk;
    std::string error;
    if (!fsv::NvRuntime::instance().acquire(bytes, pointer, error)) {
        fsv::nv_set_error(error);
        return kDriverFailure;
    }
    if (!fsv::NvRuntime::instance().upload(pointer, host, bytes, error)) {
        fsv::nv_set_error(error);
        fsv::NvRuntime::instance().recycle(pointer, bytes);
        return kDriverFailure;
    }
    std::lock_guard<std::mutex> guard(g_constant_mutex);
    ConstantEntry entry;
    entry.bytes = bytes;
    entry.pointer = pointer;
    g_constants[host] = entry;
    return kOk;
}

void fsv_cuda_constant_unregister(const void* host) {
    if (!host) return;
    fsv::NvPtr pointer = 0;
    std::size_t bytes = 0;
    {
        std::lock_guard<std::mutex> guard(g_constant_mutex);
        auto iterator = g_constants.find(host);
        if (iterator == g_constants.end()) return;
        pointer = iterator->second.pointer;
        bytes = iterator->second.bytes;
        g_constants.erase(iterator);
    }
    fsv::NvRuntime::instance().release(pointer, bytes);
}

void fsv_cuda_constant_clear(void) {
    std::unordered_map<const void*, ConstantEntry> entries;
    {
        std::lock_guard<std::mutex> guard(g_constant_mutex);
        entries.swap(g_constants);
    }
    for (const auto& entry : entries) {
        fsv::NvRuntime::instance().release(entry.second.pointer, entry.second.bytes);
    }
}

int fsv_cuda_device_count(void) {
    return fsv::NvRuntime::instance().device_count();
}

int fsv_cuda_get_device_info(int index, fsv_cuda_device_info* out) {
    if (!out || index < 0) {
        fsv::nv_set_error("invalid device index");
        return kInvalidArgument;
    }
    int major = 0;
    int minor = 0;
    std::size_t memory = 0;
    if (!fsv::NvRuntime::instance().device_info(index, &major, &minor, &memory,
                                                out->name, sizeof(out->name))) {
        return kDriverFailure;
    }
    out->index = index;
    out->major = major;
    out->minor = minor;
    out->global_memory_bytes = memory;
    fsv::nv_set_error("");
    return kOk;
}

const char* fsv_cuda_last_error(void) { return fsv::nv_last_error(); }

int fsv_cuda_active_device(char* name, size_t name_size) {
    const int index = fsv::nv_active_device(name, name_size);
    if (index < 0) return -1;
    fsv::nv_set_error("");
    return index;
}

int fsv_cuda_take_alloc_failure(void) { return fsv::nv_take_alloc_failure() ? 1 : 0; }

int fsv_cuda_binary_f32(const float* a, const float* b, float* c, size_t count, int operation) {
    if (!a || !b || !c || !count || operation < 0 || operation > 2) {
        fsv::nv_set_error("invalid binary operator arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_binary_f32", count, a, b, c,
                   static_cast<unsigned long long>(count), operation)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_unary_f32(const float* a, float* c, size_t count, int operation) {
    if (!a || !c || !count || operation < 0 || operation > 6) {
        fsv::nv_set_error("invalid unary operator arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_unary_f32", count, a, c,
                   static_cast<unsigned long long>(count), operation)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_binary_f32_host(const float* a, const float* b, float* c, size_t count, int operation) {
    if (!a || !b || !c || !count || operation < 0 || operation > 2) {
        fsv::nv_set_error("invalid binary operator arguments");
        return kInvalidArgument;
    }
    std::size_t bytes = count * sizeof(float);
    DeviceBuffer left, right, output;
    if (!to_device(a, bytes, left)) return kDriverFailure;
    if (!to_device(b, bytes, right)) return kDriverFailure;
    if (!output.reserve(bytes)) return kDriverFailure;
    int status = fsv_cuda_binary_f32(left.read<float>(), right.read<float>(),
                                     output.write<float>(), count, operation);
    if (fail(status)) return status;
    return from_device(c, output, bytes) ? kOk : kDriverFailure;
}

int fsv_cuda_unary_f32_host(const float* a, float* c, size_t count, int operation) {
    if (!a || !c || !count || operation < 0 || operation > 6) {
        fsv::nv_set_error("invalid unary operator arguments");
        return kInvalidArgument;
    }
    std::size_t bytes = count * sizeof(float);
    DeviceBuffer input, output;
    if (!to_device(a, bytes, input)) return kDriverFailure;
    if (!output.reserve(bytes)) return kDriverFailure;
    int status = fsv_cuda_unary_f32(input.read<float>(), output.write<float>(), count, operation);
    if (fail(status)) return status;
    return from_device(c, output, bytes) ? kOk : kDriverFailure;
}

int fsv_cuda_layernorm_f32(const float* input, const float* scale, const float* bias,
                           float* output, int rows, int width, float epsilon) {
    if (!input || !output || rows <= 0 || width <= 0) {
        fsv::nv_set_error("invalid layernorm arguments");
        return kInvalidArgument;
    }
    /* Eight warps per block, one row each. */
    if (!launch("fsv_layernorm_f32", static_cast<unsigned>((rows + 7) / 8), 1, 256, 1,
                input, scale, bias, output, rows, width, epsilon)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_softmax_f32(const float* input, float* output, int rows, int width) {
    if (!input || !output || rows <= 0 || width <= 0) {
        fsv::nv_set_error("invalid softmax arguments");
        return kInvalidArgument;
    }
    if (!launch("fsv_softmax_f32", static_cast<unsigned>((rows + 7) / 8), 1, 256, 1,
                input, output, rows, width)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_layernorm_f32_host(const float* input, const float* scale, const float* bias,
                                float* output, int rows, int width, float epsilon) {
    if (!input || !output || rows <= 0 || width <= 0) {
        fsv::nv_set_error("invalid layernorm arguments");
        return kInvalidArgument;
    }
    /* scale and bias are broadcast over the rows, not repeated per row. */
    std::size_t count = static_cast<std::size_t>(rows) * width;
    DeviceBuffer activation, scale_buffer, bias_buffer, result;
    if (!to_device(input, count * sizeof(float), activation)) return kDriverFailure;
    if (scale && !to_device(scale, static_cast<std::size_t>(width) * sizeof(float), scale_buffer)) {
        return kDriverFailure;
    }
    if (bias && !to_device(bias, static_cast<std::size_t>(width) * sizeof(float), bias_buffer)) {
        return kDriverFailure;
    }
    if (!result.reserve(count * sizeof(float))) return kDriverFailure;
    int status = fsv_cuda_layernorm_f32(
        activation.read<float>(),
        scale ? scale_buffer.read<float>() : nullptr,
        bias ? bias_buffer.read<float>() : nullptr,
        result.write<float>(), rows, width, epsilon);
    if (fail(status)) return status;
    return from_device(output, result, count * sizeof(float)) ? kOk : kDriverFailure;
}

int fsv_cuda_softmax_f32_host(const float* input, float* output, int rows, int width) {
    if (!input || !output || rows <= 0 || width <= 0) {
        fsv::nv_set_error("invalid softmax arguments");
        return kInvalidArgument;
    }
    std::size_t count = static_cast<std::size_t>(rows) * width;
    DeviceBuffer activation, result;
    if (!to_device(input, count * sizeof(float), activation)) return kDriverFailure;
    if (!result.reserve(count * sizeof(float))) return kDriverFailure;
    int status = fsv_cuda_softmax_f32(activation.read<float>(), result.write<float>(), rows, width);
    if (fail(status)) return status;
    return from_device(output, result, count * sizeof(float)) ? kOk : kDriverFailure;
}

int fsv_cuda_matmul_f32(const float* a, const float* b, float* c, int m, int k, int n) {
    if (!a || !b || !c || m <= 0 || k <= 0 || n <= 0) {
        fsv::nv_set_error("invalid matrix arguments");
        return kInvalidArgument;
    }
    unsigned block_x = 16;
    unsigned block_y = 16;
    unsigned grid_x = (static_cast<unsigned>(n) + block_x - 1) / block_x;
    unsigned grid_y = (static_cast<unsigned>(m) + block_y - 1) / block_y;
    if (!launch("fsv_matmul_f32", grid_x, grid_y, block_x, block_y,
                a, b, c, m, k, n, 1.0f)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_f32_host(const float* a, const float* b, float* c, int m, int k, int n) {
    if (!a || !b || !c || m <= 0 || k <= 0 || n <= 0) {
        fsv::nv_set_error("invalid matrix arguments");
        return kInvalidArgument;
    }
    std::size_t a_bytes = static_cast<std::size_t>(m) * k * sizeof(float);
    std::size_t b_bytes = static_cast<std::size_t>(k) * n * sizeof(float);
    std::size_t c_bytes = static_cast<std::size_t>(m) * n * sizeof(float);
    DeviceBuffer left, right, result;
    if (!to_device(a, a_bytes, left)) return kDriverFailure;
    if (!to_device(b, b_bytes, right)) return kDriverFailure;
    if (!result.reserve(c_bytes)) return kDriverFailure;
    int status = fsv_cuda_matmul_f32(left.read<float>(), right.read<float>(),
                                     result.write<float>(), m, k, n);
    if (fail(status)) return status;
    return from_device(c, result, c_bytes) ? kOk : kDriverFailure;
}

/* Launch geometry for the convolution. The tiled kernel puts one thread on a
   spatial position and OC_TILE output channels, so the flat grid becomes
   (spatial, channel block, batch); the untiled kernel stays for shapes whose
   channel count does not divide into blocks. */
bool launch_conv2d(const float* input, const float* weight, const float* bias, float* output,
                   int batch, int channels, int height, int width, int out_channels,
                   int kernel_h, int kernel_w, int pad_top, int pad_left, int pad_bottom,
                   int pad_right, int stride_h, int stride_w, int dilation_h, int dilation_w,
                   int groups, int out_h, int out_w) {
    const long long spatial = static_cast<long long>(out_h) * out_w;
    /* The input plane is re-read once per tile of output channels, so the
       widest legal tile is the fastest one: the exported 1x1 convolutions are
       linear layers in disguise. The tile has to divide the per-group channel
       count, or a block would straddle two groups and read the wrong input
       plane. */
    const long long per_group = out_channels / groups;
    for (int candidate = 16; candidate >= 4; candidate /= 2) {
        if (batch > 65535 || per_group % candidate != 0 ||
            out_channels / candidate > 65535) {
            continue;
        }
        return launch3("fsv_conv2d_f32_tiled", static_cast<unsigned>((spatial + 255) / 256),
                       static_cast<unsigned>(out_channels / candidate),
                       static_cast<unsigned>(batch), 256, 1, input, weight, bias, output,
                       batch, channels, height, width, out_channels, kernel_h, kernel_w,
                       pad_top, pad_left, pad_bottom, pad_right, stride_h, stride_w,
                       dilation_h, dilation_w, groups, out_h, out_w, candidate);
    }
    const unsigned long long count = static_cast<unsigned long long>(batch) * out_channels *
                                     static_cast<unsigned long long>(spatial);
    return launch("fsv_conv2d_f32_flat", static_cast<unsigned>((count + 255ull) / 256ull), 1,
                  256, 1, input, weight, bias, output, batch, channels, height, width,
                  out_channels, kernel_h, kernel_w, pad_top, pad_left, pad_bottom, pad_right,
                  stride_h, stride_w, dilation_h, dilation_w, groups, out_h, out_w);
}

int fsv_cuda_conv2d_f32_host(const float* input, const float* weight, const float* bias,
                             float* output, int batch, int channels, int height, int width,
                             int out_channels, int kernel_h, int kernel_w, int pad_top,
                             int pad_left, int pad_bottom, int pad_right, int stride_h,
                             int stride_w, int dilation_h, int dilation_w, int groups) {
    if (!input || !weight || !output || batch <= 0 || channels <= 0 || height <= 0 ||
        width <= 0 || out_channels <= 0 || kernel_h <= 0 || kernel_w <= 0 ||
        stride_h <= 0 || stride_w <= 0 || dilation_h <= 0 || dilation_w <= 0 || groups <= 0 ||
        channels % groups || out_channels % groups) {
        fsv::nv_set_error("invalid convolution arguments");
        return kInvalidArgument;
    }
    int out_h = (height + pad_top + pad_bottom - dilation_h * (kernel_h - 1) - 1) / stride_h + 1;
    int out_w = (width + pad_left + pad_right - dilation_w * (kernel_w - 1) - 1) / stride_w + 1;
    if (out_h <= 0 || out_w <= 0) {
        fsv::nv_set_error("invalid convolution output shape");
        return kInvalidArgument;
    }
    std::size_t input_bytes = static_cast<std::size_t>(batch) * channels * height * width * sizeof(float);
    std::size_t weight_count = static_cast<std::size_t>(out_channels) * (channels / groups) * kernel_h * kernel_w;
    std::size_t output_count = static_cast<std::size_t>(batch) * out_channels * out_h * out_w;
    DeviceBuffer activation, kernel, shift, result;
    if (!to_device(input, input_bytes, activation)) return kDriverFailure;
    if (!to_device(weight, weight_count * sizeof(float), kernel)) return kDriverFailure;
    if (bias && !to_device(bias, static_cast<std::size_t>(out_channels) * sizeof(float), shift)) {
        return kDriverFailure;
    }
    if (!result.reserve(output_count * sizeof(float))) return kDriverFailure;
    if (!launch_conv2d(activation.read<float>(), kernel.read<float>(),
                       bias ? shift.read<float>() : nullptr, result.write<float>(), batch,
                       channels, height, width, out_channels, kernel_h, kernel_w, pad_top,
                       pad_left, pad_bottom, pad_right, stride_h, stride_w, dilation_h,
                       dilation_w, groups, out_h, out_w)) {
        return kDriverFailure;
    }
    return from_device(output, result, output_count * sizeof(float)) ? kOk : kDriverFailure;
}

int fsv_cuda_conv_transpose1d_f32_host(const float* input, const float* weight, const float* bias,
                                       float* output, int batch, int channels, int width,
                                       int out_channels, int kernel, int pad_left, int pad_right,
                                       int stride, int dilation, int groups, int output_padding) {
    (void)input; (void)weight; (void)bias; (void)output; (void)batch; (void)channels;
    if (!input || !weight || !output || batch <= 0 || channels <= 0 || width <= 0 ||
        out_channels <= 0 || kernel <= 0 || stride <= 0 || dilation <= 0 || groups <= 0 ||
        channels % groups || out_channels % groups || output_padding < 0 ||
        output_padding >= stride) {
        fsv::nv_set_error("invalid conv_transpose arguments");
        return kInvalidArgument;
    }
    int out_width = (width - 1) * stride - pad_left - pad_right + dilation * (kernel - 1) +
                    output_padding + 1;
    if (out_width <= 0) {
        fsv::nv_set_error("invalid conv_transpose output width");
        return kInvalidArgument;
    }
    std::size_t input_bytes = static_cast<std::size_t>(batch) * channels * width * sizeof(float);
    std::size_t weight_count = static_cast<std::size_t>(channels) * (out_channels / groups) * kernel;
    std::size_t output_count = static_cast<std::size_t>(batch) * out_channels * out_width;
    DeviceBuffer activation, taps, shift, result;
    if (!to_device(input, input_bytes, activation)) return kDriverFailure;
    if (!to_device(weight, weight_count * sizeof(float), taps)) return kDriverFailure;
    if (bias && !to_device(bias, static_cast<std::size_t>(out_channels) * sizeof(float), shift)) {
        return kDriverFailure;
    }
    if (!result.reserve(output_count * sizeof(float))) return kDriverFailure;
    unsigned long long blocks = (output_count + 255ull) / 256ull;
    if (!launch("fsv_conv_transpose1d_f32", static_cast<unsigned>(blocks), 1, 256, 1,
                activation.read<float>(),
                taps.read<float>(),
                bias ? shift.read<float>() : nullptr,
                result.write<float>(), batch, channels, width, out_channels,
                kernel, pad_left, stride, dilation, groups, out_width)) {
        return kDriverFailure;
    }
    return from_device(output, result, output_count * sizeof(float)) ? kOk : kDriverFailure;
}

int fsv_cuda_matmul_i8(const signed char* a, const signed char* b, float* c,
                       int m, int k, int n, float a_scale, float b_scale) {
    if (!a || !b || !c || m <= 0 || k <= 0 || n <= 0) {
        fsv::nv_set_error("invalid matrix arguments");
        return kInvalidArgument;
    }
    unsigned grid_x = (static_cast<unsigned>(n) + 15) / 16;
    unsigned grid_y = (static_cast<unsigned>(m) + 15) / 16;
    if (!launch("fsv_matmul_i8", grid_x, grid_y, 16, 16, a, b, c, m, k, n, a_scale * b_scale)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_i4(const signed char* a, const unsigned char* packed_b, float* c,
                       int m, int k, int n, float a_scale, float b_scale) {
    if (!a || !packed_b || !c || m <= 0 || k <= 0 || n <= 0) {
        fsv::nv_set_error("invalid matrix arguments");
        return kInvalidArgument;
    }
    unsigned grid_x = (static_cast<unsigned>(n) + 15) / 16;
    unsigned grid_y = (static_cast<unsigned>(m) + 15) / 16;
    if (!launch("fsv_matmul_i4", grid_x, grid_y, 16, 16, a, packed_b, c, m, k, n,
                a_scale * b_scale)) {
        return kDriverFailure;
    }
    return kOk;
}

/* Launch geometry for the INT4 product.

   One lane per output column needs m*n lanes, which starves the device once
   the row count is small: the decoder's cached steps are m == 1, so the whole
   kernel would fit in a handful of blocks. Splitting the reduction across a
   few lanes per column restores the parallelism, at the cost of a shuffle
   reduction, and it stops helping as soon as the per-lane slice gets short. */
void nbits_launch_shape(int m, int k, int n, int& split, int& values_per_lane) {
    split = 1;
    /* A cached step is m == 1, so the output columns are the only source of
       parallelism the product has: [1, 2048] x [2048, 512] is 512 outputs, and
       stopping the search at eight lanes per column left the card three
       quarters idle. Splitting further is nearly free while each lane still
       walks a useful run, so the search runs to a full warp per column. */
    for (int candidate = 2; candidate <= 32; candidate *= 2) {
        if (k / candidate < 32) break;
        const long long columns_per_warp = 32 / candidate;
        const long long warps =
            (static_cast<long long>(m) * n + columns_per_warp - 1) / columns_per_warp;
        split = candidate;
        if (warps >= 1024) break;
    }
    /* Each lane walks an even number of values so its slice starts on a byte
       boundary and never splits the low nibble from the high one. When there
       is a whole sixteen-byte group to fill, rounding the slice up to one
       keeps the packed read aligned for the kernel's uint4 path; the lanes
       that fall past the end of the reduction simply contribute nothing. */
    const int slice = (k + split - 1) / split;
    values_per_lane = slice >= 32 ? (slice + 31) & ~31 : (slice + 1) & ~1;
}

bool launch_nbits4(const float* a, const unsigned char* packed_b, const float* scales,
                   const unsigned short* scales_f16, float* c, int m, int k, int n,
                   int block_size) {
    int split = 1;
    int values_per_lane = k;
    nbits_launch_shape(m, k, n, split, values_per_lane);
    const long long columns_per_warp = 32 / split;
    const long long warps =
        (static_cast<long long>(m) * n + columns_per_warp - 1) / columns_per_warp;
    const unsigned grid_x = static_cast<unsigned>((warps + 7) / 8);
    return launch("fsv_matmul_nbits4_f32", grid_x, 1, 256, 1, a, packed_b, scales,
                  scales_f16, c, m, k, n, block_size, values_per_lane, split);
}

int fsv_cuda_matmul_nbits_f32(const float* a, const unsigned char* packed_b,
                              const float* scales, float* c, int m, int k, int n,
                              int block_size, int bits) {
    if (!a || !packed_b || !scales || !c || m <= 0 || k <= 0 || n <= 0 ||
        block_size <= 0 || bits != 4) {
        fsv::nv_set_error("invalid quantized matrix arguments");
        return kInvalidArgument;
    }
    if ((block_size & 1) == 0) {
        if (!launch_nbits4(a, packed_b, scales, static_cast<const unsigned short*>(nullptr),
                           c, m, k, n, block_size)) {
            return kDriverFailure;
        }
        return kOk;
    }
    unsigned grid_x = (static_cast<unsigned>(n) + 15) / 16;
    unsigned grid_y = (static_cast<unsigned>(m) + 15) / 16;
    if (!launch("fsv_matmul_nbits_f32", grid_x, grid_y, 16, 16, a, packed_b, scales, c,
                m, k, n, block_size, bits)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_nbits_f16scales(const float* a, const unsigned char* packed_b,
                                    const unsigned short* scales, float* c, int m, int k, int n,
                                    int block_size, int bits) {
    if (!a || !packed_b || !scales || !c || m <= 0 || k <= 0 || n <= 0 ||
        block_size <= 0 || bits != 4) {
        fsv::nv_set_error("invalid quantized matrix arguments");
        return kInvalidArgument;
    }
    if ((block_size & 1) == 0) {
        if (!launch_nbits4(a, packed_b, static_cast<const float*>(nullptr), scales, c, m, k,
                           n, block_size)) {
            return kDriverFailure;
        }
        return kOk;
    }
    unsigned grid_x = (static_cast<unsigned>(n) + 15) / 16;
    unsigned grid_y = (static_cast<unsigned>(m) + 15) / 16;
    if (!launch("fsv_matmul_nbits_f16scale", grid_x, grid_y, 16, 16, a, packed_b, scales, c,
                m, k, n, block_size, bits)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_nbits_f32_host(const float* a, const unsigned char* packed_b,
                                   const float* scales, float* c, int m, int k, int n,
                                   int block_size, int bits) {
    if (!a || !packed_b || !scales || !c || m <= 0 || k <= 0 || n <= 0 ||
        block_size <= 0 || bits != 4) {
        fsv::nv_set_error("invalid quantized matrix arguments");
        return kInvalidArgument;
    }
    int blocks = (k + block_size - 1) / block_size;
    std::size_t a_bytes = static_cast<std::size_t>(m) * k * sizeof(float);
    std::size_t b_bytes = static_cast<std::size_t>(n) * blocks * (block_size * bits / 8);
    std::size_t scale_bytes = static_cast<std::size_t>(n) * blocks * sizeof(float);
    std::size_t c_bytes = static_cast<std::size_t>(m) * n * sizeof(float);
    DeviceBuffer activation, weight, scale_buffer, result;
    if (!to_device(a, a_bytes, activation)) return kDriverFailure;
    if (!to_device(packed_b, b_bytes, weight)) return kDriverFailure;
    if (!to_device(scales, scale_bytes, scale_buffer)) return kDriverFailure;
    if (!result.reserve(c_bytes)) return kDriverFailure;
    int status = fsv_cuda_matmul_nbits_f32(
        activation.read<float>(), weight.read<unsigned char>(), scale_buffer.read<float>(),
        result.write<float>(), m, k, n, block_size, bits);
    if (fail(status)) return status;
    return from_device(c, result, c_bytes) ? kOk : kDriverFailure;
}

int fsv_cuda_matmul_nbits_f16scales_host(const float* a, const unsigned char* packed_b,
                                         const unsigned short* scales, float* c, int m, int k,
                                         int n, int block_size, int bits) {
    if (!a || !packed_b || !scales || !c || m <= 0 || k <= 0 || n <= 0 ||
        block_size <= 0 || bits != 4) {
        fsv::nv_set_error("invalid quantized matrix arguments");
        return kInvalidArgument;
    }
    int blocks = (k + block_size - 1) / block_size;
    std::size_t a_bytes = static_cast<std::size_t>(m) * k * sizeof(float);
    std::size_t b_bytes = static_cast<std::size_t>(n) * blocks * (block_size * bits / 8);
    std::size_t scale_bytes = static_cast<std::size_t>(n) * blocks * sizeof(unsigned short);
    std::size_t c_bytes = static_cast<std::size_t>(m) * n * sizeof(float);
    DeviceBuffer activation, weight, scale_buffer, result;
    if (!to_device(a, a_bytes, activation)) return kDriverFailure;
    if (!to_device(packed_b, b_bytes, weight)) return kDriverFailure;
    if (!to_device(scales, scale_bytes, scale_buffer)) return kDriverFailure;
    if (!result.reserve(c_bytes)) return kDriverFailure;
    int status = fsv_cuda_matmul_nbits_f16scales(
        activation.read<float>(), weight.read<unsigned char>(), scale_buffer.read<unsigned short>(),
        result.write<float>(), m, k, n, block_size, bits);
    if (fail(status)) return status;
    return from_device(c, result, c_bytes) ? kOk : kDriverFailure;
}


/* -------------------------------------------------------------------------
   Device-resident tensors.

   The graph runtime keeps whole tensors in device memory. These entry points
   take driver pointers it owns, queue kernels on the default stream and never
   wait: only an explicit download or sync stops the pipeline. That is what
   lets a graph of thousands of operators run without a host round trip
   between them.
   ------------------------------------------------------------------------- */

/* Mirrors FsvIndex in src/kernels/fsv_kernels.cu. */
struct DeviceIndex {
    long long shape[8];
    long long a_stride[8];
    long long b_stride[8];
    int rank;
    int operation;
};

bool copy_index(const fsv_cuda_index* source, DeviceIndex& target) {
    if (!source || source->rank < 0 || source->rank > 8) return false;
    std::memcpy(target.shape, source->shape, sizeof(target.shape));
    std::memcpy(target.a_stride, source->a_stride, sizeof(target.a_stride));
    std::memcpy(target.b_stride, source->b_stride, sizeof(target.b_stride));
    target.rank = source->rank;
    target.operation = source->operation;
    return true;
}

/* Mirrors FsvWhereIndex in src/kernels/fsv_kernels.cu. */
struct DeviceWhereIndex {
    long long shape[8];
    long long condition_stride[8];
    long long a_stride[8];
    long long b_stride[8];
    int rank;
    int reserved;
};

bool copy_where_index(const fsv_cuda_where_index* source, DeviceWhereIndex& target) {
    if (!source || source->rank < 0 || source->rank > 8) return false;
    std::memcpy(target.shape, source->shape, sizeof(target.shape));
    std::memcpy(target.condition_stride, source->condition_stride,
                sizeof(target.condition_stride));
    std::memcpy(target.a_stride, source->a_stride, sizeof(target.a_stride));
    std::memcpy(target.b_stride, source->b_stride, sizeof(target.b_stride));
    target.rank = source->rank;
    target.reserved = 0;
    return true;
}

/* True when ``strides`` walks the indexed box contiguously: each dimension
   other than a unit one has to step by exactly the product of the dimensions
   after it. A broadcast operand never passes, because a dimension it repeats
   carries a zero stride. */
static bool contiguous_strides_of(const long long* shape, int rank,
                                  const long long* strides) {
    long long expected = 1;
    for (int dim = rank - 1; dim >= 0; --dim) {
        const long long size = shape[dim];
        if (size <= 0) return false;
        if (size > 1 && strides[dim] != expected) return false;
        expected *= size;
    }
    return true;
}

/* True when an operand contributes the same element to every output slot,
   which is what a one-element tensor broadcasting over a larger shape does. */
static bool all_zero_strides_of(int rank, const long long* strides) {
    for (int dim = 0; dim < rank; ++dim) {
        if (strides[dim] != 0) return false;
    }
    return true;
}

/* The two index blocks carry the same shape and rank fields but different
   stride rows. Overloads cannot live in this translation unit's extern "C"
   block, so the Where caller names the shared body directly. */
bool contiguous_strides(const DeviceIndex& spec, const long long* strides) {
    return contiguous_strides_of(spec.shape, spec.rank, strides);
}

bool all_zero_strides(const DeviceIndex& spec, const long long* strides) {
    return all_zero_strides_of(spec.rank, strides);
}

/* Grid for a kernel that moves four elements per thread. */
unsigned vector_blocks(unsigned long long count) {
    const unsigned long long threads = (count + 3ull) / 4ull;
    return static_cast<unsigned>((threads + 255ull) / 256ull);
}

int fsv_cuda_device_alloc(size_t bytes, fsv_cuda_ptr* pointer) {
    if (!pointer || !bytes) {
        fsv::nv_set_error("invalid device allocation");
        return kInvalidArgument;
    }
    fsv::NvPtr address = 0;
    std::string error;
    if (!fsv::NvRuntime::instance().acquire(bytes, address, error)) {
        fsv::nv_set_error(error);
        return kDriverFailure;
    }
    *pointer = address;
    return kOk;
}

void fsv_cuda_device_free(fsv_cuda_ptr pointer, size_t bytes) {
    if (!pointer) return;
    fsv::NvRuntime::instance().recycle(pointer, bytes);
}

int fsv_cuda_device_upload(fsv_cuda_ptr pointer, const void* host, size_t bytes) {
    if (!pointer || !host || !bytes) {
        fsv::nv_set_error("invalid device upload");
        return kInvalidArgument;
    }
    std::string error;
    if (!fsv::NvRuntime::instance().upload(pointer, host, bytes, error)) {
        fsv::nv_set_error(error);
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_device_download(void* host, fsv_cuda_ptr pointer, size_t bytes) {
    if (!pointer || !host || !bytes) {
        fsv::nv_set_error("invalid device download");
        return kInvalidArgument;
    }
    std::string error;
    if (!fsv::NvRuntime::instance().download(host, pointer, bytes, error)) {
        fsv::nv_set_error(error);
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_device_sync(void) {
    std::string error;
    if (!fsv::NvRuntime::instance().synchronize(error)) {
        fsv::nv_set_error(error);
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_constant_lookup(const void* host, size_t bytes, fsv_cuda_ptr* pointer) {
    if (!host || !bytes || !pointer) return kInvalidArgument;
    fsv::NvPtr address = 0;
    if (!constant_lookup(host, bytes, address)) return kInvalidArgument;
    *pointer = address;
    return kOk;
}

int fsv_cuda_binary_bcast_f32(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c, size_t count,
                              const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!a || !b || !c || !count || !copy_index(index, spec)) {
        fsv::nv_set_error("invalid broadcast arguments");
        return kInvalidArgument;
    }
    /* Both operands laid out like the result is the case the exported graphs
       spend their element-wise time on, and it needs neither a broadcast rule
       nor a coordinate walk: four elements per thread instead. */
    if (contiguous_strides(spec, spec.a_stride) && contiguous_strides(spec, spec.b_stride)) {
        if (!launch("fsv_binary_contiguous_f32", vector_blocks(count), 1, 256, 1,
                    device_pointer<float>(a), device_pointer<float>(b),
                    reinterpret_cast<float*>(c),
                    static_cast<unsigned long long>(count), spec.operation)) {
            return kDriverFailure;
        }
        return kOk;
    }
    /* One operand is a single value broadcast over the other. The generic
       kernel walks the index once per element to read that same value back, so
       a dedicated kernel loads it once and moves four elements per thread. */
    const bool left_scalar = all_zero_strides(spec, spec.a_stride);
    const bool right_scalar = all_zero_strides(spec, spec.b_stride);
    if (left_scalar != right_scalar) {
        const long long* operand_strides = left_scalar ? spec.b_stride : spec.a_stride;
        if (contiguous_strides(spec, operand_strides)) {
            if (!launch("fsv_binary_scalar_f32", vector_blocks(count), 1, 256, 1,
                        device_pointer<float>(left_scalar ? b : a),
                        device_pointer<float>(left_scalar ? a : b),
                        reinterpret_cast<float*>(c),
                        static_cast<unsigned long long>(count), spec.operation,
                        left_scalar ? 1 : 0)) {
                return kDriverFailure;
            }
            return kOk;
        }
    }
    if (!launch_1d("fsv_binary_bcast_f32", count, device_pointer<float>(a),
                   device_pointer<float>(b), reinterpret_cast<float*>(c),
                   static_cast<unsigned long long>(count), spec)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_compare_bcast_u8(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c, size_t count,
                              const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!a || !b || !c || !count || !copy_index(index, spec)) {
        fsv::nv_set_error("invalid comparison arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_compare_bcast_u8", count, device_pointer<float>(a),
                   device_pointer<float>(b), reinterpret_cast<unsigned char*>(c),
                   static_cast<unsigned long long>(count), spec)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_where_f32(fsv_cuda_ptr condition, fsv_cuda_ptr a, fsv_cuda_ptr b,
                       fsv_cuda_ptr out, size_t count,
                       const fsv_cuda_where_index* index) {
    DeviceWhereIndex spec{};
    if (!condition || !a || !b || !out || !count || !copy_where_index(index, spec)) {
        fsv::nv_set_error("invalid where arguments");
        return kInvalidArgument;
    }
    /* All three operands laid out like the result is the decoder's mask build;
       four results and four condition bytes move per thread. */
    if (contiguous_strides_of(spec.shape, spec.rank, spec.condition_stride) &&
        contiguous_strides_of(spec.shape, spec.rank, spec.a_stride) &&
        contiguous_strides_of(spec.shape, spec.rank, spec.b_stride)) {
        if (!launch("fsv_where_contiguous_f32", vector_blocks(count), 1, 256, 1,
                    device_pointer<unsigned char>(condition), device_pointer<float>(a),
                    device_pointer<float>(b), reinterpret_cast<float*>(out),
                    static_cast<unsigned long long>(count))) {
            return kDriverFailure;
        }
        return kOk;
    }
    /* One operand is a single value broadcast over the condition, which is how
       the attention masks pick their fill value. */
    const bool left_scalar = all_zero_strides_of(spec.rank, spec.a_stride);
    const bool right_scalar = all_zero_strides_of(spec.rank, spec.b_stride);
    if (left_scalar != right_scalar &&
        contiguous_strides_of(spec.shape, spec.rank, spec.condition_stride)) {
        const long long* operand_strides = left_scalar ? spec.b_stride : spec.a_stride;
        if (contiguous_strides_of(spec.shape, spec.rank, operand_strides)) {
            if (!launch("fsv_where_scalar_f32", vector_blocks(count), 1, 256, 1,
                        device_pointer<unsigned char>(condition),
                        device_pointer<float>(left_scalar ? b : a),
                        device_pointer<float>(left_scalar ? a : b),
                        reinterpret_cast<float*>(out),
                        static_cast<unsigned long long>(count), left_scalar ? 1 : 0)) {
                return kDriverFailure;
            }
            return kOk;
        }
    }
    if (!launch_1d("fsv_where_bcast_f32", count, device_pointer<unsigned char>(condition),
                   device_pointer<float>(a), device_pointer<float>(b),
                   reinterpret_cast<float*>(out),
                   static_cast<unsigned long long>(count), spec)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_unary_ops_f32(fsv_cuda_ptr a, fsv_cuda_ptr c, size_t count, int operation,
                           float alpha) {
    if (!a || !c || !count || operation < 0 || operation > 12) {
        fsv::nv_set_error("invalid unary operator arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_unary_ops_f32", count, device_pointer<float>(a),
                   reinterpret_cast<float*>(c),
                   static_cast<unsigned long long>(count), operation, alpha)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_clamp_f32(fsv_cuda_ptr a, fsv_cuda_ptr c, size_t count, int has_low, float low,
                       int has_high, float high) {
    if (!a || !c || !count) {
        fsv::nv_set_error("invalid clip arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_clamp_f32", count, device_pointer<float>(a),
                   reinterpret_cast<float*>(c),
                   static_cast<unsigned long long>(count), has_low, low, has_high, high)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_channel_affine_f32(fsv_cuda_ptr input, fsv_cuda_ptr scale, fsv_cuda_ptr shift,
                                fsv_cuda_ptr output, long long batch, long long channels,
                                long long spatial) {
    if (!input || !output || batch <= 0 || channels <= 0 || spatial <= 0) {
        fsv::nv_set_error("invalid channel affine arguments");
        return kInvalidArgument;
    }
    unsigned long long count = static_cast<unsigned long long>(batch) *
        static_cast<unsigned long long>(channels) * static_cast<unsigned long long>(spatial);
    if (!launch_1d("fsv_channel_affine_f32", count, device_pointer<float>(input),
                   scale ? device_pointer<float>(scale) : nullptr,
                   shift ? device_pointer<float>(shift) : nullptr,
                   reinterpret_cast<float*>(output), batch, channels, spatial)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_transpose_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination, size_t count,
                           int rank, const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!source || !destination || !copy_index(index, spec) || rank <= 0 || rank > 8) {
        fsv::nv_set_error("invalid transpose arguments");
        return kInvalidArgument;
    }
    spec.rank = rank;
    if (!launch_1d("fsv_transpose_f32", count, device_pointer<float>(source),
                   reinterpret_cast<float*>(destination),
                   static_cast<unsigned long long>(count), rank, spec)) {
        return kDriverFailure;
    }
    return kOk;
}

/* Batched transpose of a [batch][rows][cols] block into [batch][cols][rows]. */
int fsv_cuda_transpose_tile_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                long long batch, long long rows, long long cols) {
    if (!source || !destination || batch <= 0 || rows <= 0 || cols <= 0) {
        fsv::nv_set_error("invalid tiled transpose arguments");
        return kInvalidArgument;
    }
    /* blockIdx.z holds the batch, and it stops at 65535, so a longer stack is
       issued in chunks with the pointers moved along. */
    const unsigned grid_x = static_cast<unsigned>((cols + 31) / 32);
    const unsigned grid_y = static_cast<unsigned>((rows + 31) / 32);
    for (long long base = 0; base < batch; base += 65536) {
        const long long remaining = batch - base;
        const unsigned grid_z = static_cast<unsigned>(remaining < 65536 ? remaining : 65536);
        const float* source_at = device_pointer<float>(source) + base * rows * cols;
        float* destination_at = reinterpret_cast<float*>(destination) + base * rows * cols;
        if (!launch3("fsv_transpose_tile_f32", grid_x, grid_y, grid_z, 32, 32,
                     source_at, destination_at, rows, cols)) {
            return kDriverFailure;
        }
    }
    return kOk;
}

/* Exchange two axes that each carry a contiguous run of `inner` values. */
int fsv_cuda_transpose_swap_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                                long long a_size, long long b_size, long long inner) {
    if (!source || !destination || a_size <= 0 || b_size <= 0 || inner <= 0) {
        fsv::nv_set_error("invalid axis-swap arguments");
        return kInvalidArgument;
    }
    const unsigned long long count = static_cast<unsigned long long>(a_size) *
                                     static_cast<unsigned long long>(b_size);
    if (!launch_1d("fsv_transpose_swap_f32", count, device_pointer<float>(source),
                   reinterpret_cast<float*>(destination), a_size, b_size, inner)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_copy_nd_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination, size_t count,
                         long long source_base, long long destination_base, int rank,
                         const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!source || !destination || !copy_index(index, spec) || rank < 0 || rank > 8) {
        fsv::nv_set_error("invalid copy arguments");
        return kInvalidArgument;
    }
    spec.rank = rank;
    /* A box that is contiguous at both ends is a plain run-to-run copy, which
       is what a trailing Slice or a whole Concat operand is. The vector load
       needs the element offsets to fall on a float4 boundary. */
    if ((source_base & 3) == 0 && (destination_base & 3) == 0 &&
        contiguous_strides(spec, spec.a_stride) &&
        contiguous_strides(spec, spec.b_stride)) {
        if (!launch("fsv_copy_contiguous_f32", vector_blocks(count), 1, 256, 1,
                    device_pointer<float>(source) + source_base,
                    reinterpret_cast<float*>(destination) + destination_base,
                    static_cast<unsigned long long>(count))) {
            return kDriverFailure;
        }
        return kOk;
    }
    if (!launch_1d("fsv_copy_nd_f32", count, device_pointer<float>(source),
                   reinterpret_cast<float*>(destination),
                   static_cast<unsigned long long>(count), source_base, destination_base,
                   rank, spec)) {
        return kDriverFailure;
    }
    return kOk;
}

/* Multiply a contiguous run of floats by a scalar that travels as a kernel
   argument. Gemm used to build a one-element tensor, upload it and run a
   broadcast pass for every node it evaluated. */
int fsv_cuda_scale_into_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                            size_t count, float factor) {
    if (!source || !destination || !count) {
        fsv::nv_set_error("invalid scale arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_scale_into_f32", count, device_pointer<float>(source),
                   reinterpret_cast<float*>(destination),
                   static_cast<unsigned long long>(count), factor)) {
        return kDriverFailure;
    }
    return kOk;
}

/* Reduce a run of consecutive dimensions: [outer][mid][inner] -> [outer][inner].
   The caller only routes a node here when its axes name such a run, so the
   kernel needs no coordinate walk. */
int fsv_cuda_reduce_block_f32(fsv_cuda_ptr source, fsv_cuda_ptr destination,
                              long long outer, long long mid, long long inner,
                              int operation) {
    if (!source || !destination || outer <= 0 || mid <= 0 || inner <= 0 ||
        operation < 0 || operation > 3) {
        fsv::nv_set_error("invalid reduce arguments");
        return kInvalidArgument;
    }
    const unsigned long long count =
        static_cast<unsigned long long>(outer) * static_cast<unsigned long long>(inner);
    if (!launch_1d("fsv_reduce_block_f32", count, device_pointer<float>(source),
                   reinterpret_cast<float*>(destination), outer, mid, inner, operation)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_fill_f32(fsv_cuda_ptr destination, size_t count, float value) {
    if (!destination || !count) {
        fsv::nv_set_error("invalid fill arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_fill_f32", count, reinterpret_cast<float*>(destination),
                   static_cast<unsigned long long>(count), value)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_gather_f32(fsv_cuda_ptr data, fsv_cuda_ptr indices, fsv_cuda_ptr output,
                        long long outer, long long index_count, long long inner,
                        long long axis_size) {
    if (!data || !indices || !output || outer < 0 || index_count < 0 || inner <= 0 ||
        axis_size <= 0) {
        fsv::nv_set_error("invalid gather arguments");
        return kInvalidArgument;
    }
    unsigned long long count = static_cast<unsigned long long>(outer) *
        static_cast<unsigned long long>(index_count) * static_cast<unsigned long long>(inner);
    if (!count) return kOk;
    if (!launch_1d("fsv_gather_f32", count, device_pointer<float>(data),
                   reinterpret_cast<const long long*>(indices),
                   reinterpret_cast<float*>(output), outer, index_count, inner, axis_size)) {
        return kDriverFailure;
    }
    return kOk;
}

/* GatherElements: every output slot carries its own index for one axis, so the
   kernel walks the output coordinates and substitutes the selected position. */
int fsv_cuda_gather_elements_f32(fsv_cuda_ptr data, fsv_cuda_ptr indices,
                                 fsv_cuda_ptr output, size_t count, long long axis,
                                 long long axis_size, const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!data || !indices || !output || !count || !copy_index(index, spec) || axis < 0 ||
        axis_size <= 0) {
        fsv::nv_set_error("invalid gather-elements arguments");
        return kInvalidArgument;
    }
    if (!launch_1d("fsv_gather_elements_f32", count, device_pointer<float>(data),
                   reinterpret_cast<const long long*>(indices),
                   reinterpret_cast<float*>(output),
                   static_cast<unsigned long long>(count), axis, axis_size, spec)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_dev(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c, int m, int k, int n) {
    return fsv_cuda_matmul_f32(device_pointer<float>(a), device_pointer<float>(b),
                               reinterpret_cast<float*>(c), m, k, n);
}

int fsv_cuda_matmul_f32_batched(fsv_cuda_ptr a, fsv_cuda_ptr b, fsv_cuda_ptr c, int m, int k,
                                int n, long long batch, long long slice_offset,
                                const fsv_cuda_index* index) {
    DeviceIndex spec{};
    if (!a || !b || !c || m <= 0 || k <= 0 || n <= 0 || batch <= 0 || batch > 65535 ||
        slice_offset < 0 || !copy_index(index, spec)) {
        fsv::nv_set_error("invalid batched matrix arguments");
        return kInvalidArgument;
    }
    /* A single row is a matrix-vector product, and there the column count is
       all the parallelism a plain product has: the vector kernel splits the
       reduction across the warps of a block and folds the partials through
       shared memory. The split lives between warps, never inside one, so every
       warp still reads 32 contiguous columns of the k-major weight per step. */
    if (m == 1) {
        const unsigned gemv_grid_x = static_cast<unsigned>((n + 31) / 32);
        if (!launch3("fsv_matmul_f32_gemv", gemv_grid_x, 1, static_cast<unsigned>(batch),
                     256, 1, device_pointer<float>(a), device_pointer<float>(b),
                     reinterpret_cast<float*>(c), m, k, n, batch, slice_offset, spec)) {
            return kDriverFailure;
        }
        return kOk;
    }
    /* Several rows: one thread per four adjacent columns of one row -- the
       kernel's kMatmulColumns. The reduction is not split across lanes: the
       weight is k-major, so a warp already reads a contiguous run of columns at
       every step, and splitting k would turn each of those reads into its own
       sector. The parallelism comes from the columns per thread instead. */
    const long long groups_per_row = (static_cast<long long>(n) + 3) / 4;
    const long long groups = static_cast<long long>(m) * groups_per_row;
    const unsigned block_x = 256;
    const unsigned grid_x = static_cast<unsigned>((groups + block_x - 1) / block_x);
    if (!launch3("fsv_matmul_f32_batched", grid_x, 1, static_cast<unsigned>(batch),
                 block_x, 1, device_pointer<float>(a), device_pointer<float>(b),
                 reinterpret_cast<float*>(c), m, k, n, batch, slice_offset, spec)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_conv2d_dev(fsv_cuda_ptr input, fsv_cuda_ptr weight, fsv_cuda_ptr bias,
                        fsv_cuda_ptr output, int batch, int channels, int height, int width,
                        int out_channels, int kernel_h, int kernel_w, int pad_top,
                        int pad_left, int pad_bottom, int pad_right, int stride_h,
                        int stride_w, int dilation_h, int dilation_w, int groups) {
    if (!input || !weight || !output || batch <= 0 || channels <= 0 || height <= 0 ||
        width <= 0 || out_channels <= 0 || kernel_h <= 0 || kernel_w <= 0 ||
        stride_h <= 0 || stride_w <= 0 || dilation_h <= 0 || dilation_w <= 0 || groups <= 0 ||
        channels % groups || out_channels % groups) {
        fsv::nv_set_error("invalid convolution arguments");
        return kInvalidArgument;
    }
    int out_h = (height + pad_top + pad_bottom - dilation_h * (kernel_h - 1) - 1) / stride_h + 1;
    int out_w = (width + pad_left + pad_right - dilation_w * (kernel_w - 1) - 1) / stride_w + 1;
    if (out_h <= 0 || out_w <= 0) {
        fsv::nv_set_error("invalid convolution output shape");
        return kInvalidArgument;
    }
    if (!launch_conv2d(device_pointer<float>(input), device_pointer<float>(weight),
                       bias ? device_pointer<float>(bias) : nullptr,
                       reinterpret_cast<float*>(output), batch, channels, height, width,
                       out_channels, kernel_h, kernel_w, pad_top, pad_left, pad_bottom,
                       pad_right, stride_h, stride_w, dilation_h, dilation_w, groups, out_h,
                       out_w)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_conv_transpose1d_dev(fsv_cuda_ptr input, fsv_cuda_ptr weight, fsv_cuda_ptr bias,
                                  fsv_cuda_ptr output, int batch, int channels, int width,
                                  int out_channels, int kernel, int pad_left, int pad_right,
                                  int stride, int dilation, int groups, int output_padding) {
    if (!input || !weight || !output || batch <= 0 || channels <= 0 || width <= 0 ||
        out_channels <= 0 || kernel <= 0 || stride <= 0 || dilation <= 0 || groups <= 0 ||
        channels % groups || out_channels % groups) {
        fsv::nv_set_error("invalid conv_transpose arguments");
        return kInvalidArgument;
    }
    int out_width = (width - 1) * stride - pad_left - pad_right +
                    dilation * (kernel - 1) + output_padding + 1;
    if (out_width <= 0) {
        fsv::nv_set_error("invalid conv_transpose output width");
        return kInvalidArgument;
    }
    unsigned long long count = static_cast<unsigned long long>(batch) *
        static_cast<unsigned long long>(out_channels) *
        static_cast<unsigned long long>(out_width);
    unsigned long long blocks = (count + 255ull) / 256ull;
    if (!launch("fsv_conv_transpose1d_f32", static_cast<unsigned>(blocks), 1, 256, 1,
                device_pointer<float>(input), device_pointer<float>(weight),
                bias ? device_pointer<float>(bias) : nullptr,
                reinterpret_cast<float*>(output), batch, channels, width, out_channels,
                kernel, pad_left, stride, dilation, groups, out_width)) {
        return kDriverFailure;
    }
    return kOk;
}

int fsv_cuda_matmul_nbits_dev(fsv_cuda_ptr a, fsv_cuda_ptr packed_b, fsv_cuda_ptr scales,
                              fsv_cuda_ptr c, int m, int k, int n, int block_size, int bits,
                              int scales_are_f16) {
    if (!a || !packed_b || !scales || !c || m <= 0 || k <= 0 || n <= 0 ||
        block_size <= 0 || bits != 4) {
        fsv::nv_set_error("invalid quantized matrix arguments");
        return kInvalidArgument;
    }
    return scales_are_f16
        ? fsv_cuda_matmul_nbits_f16scales(device_pointer<float>(a),
                                          reinterpret_cast<const unsigned char*>(packed_b),
                                          reinterpret_cast<const unsigned short*>(scales),
                                          reinterpret_cast<float*>(c), m, k, n, block_size, bits)
        : fsv_cuda_matmul_nbits_f32(device_pointer<float>(a),
                                    reinterpret_cast<const unsigned char*>(packed_b),
                                    device_pointer<float>(scales),
                                    reinterpret_cast<float*>(c), m, k, n, block_size, bits);
}

int fsv_cuda_layernorm_dev(fsv_cuda_ptr input, fsv_cuda_ptr scale, fsv_cuda_ptr bias,
                           fsv_cuda_ptr output, int rows, int width, float epsilon) {
    return fsv_cuda_layernorm_f32(device_pointer<float>(input),
                                  scale ? device_pointer<float>(scale) : nullptr,
                                  bias ? device_pointer<float>(bias) : nullptr,
                                  reinterpret_cast<float*>(output), rows, width, epsilon);
}

int fsv_cuda_softmax_dev(fsv_cuda_ptr input, fsv_cuda_ptr output, int rows, int width) {
    return fsv_cuda_softmax_f32(device_pointer<float>(input),
                                reinterpret_cast<float*>(output), rows, width);
}

}  // extern "C"
