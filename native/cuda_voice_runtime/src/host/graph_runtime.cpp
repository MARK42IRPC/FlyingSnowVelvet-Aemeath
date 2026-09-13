#include "fsv_graph_runtime.h"
#include "fsv_cuda_voice_runtime.h"
#include "fsv_opstats.h"

/*
 * Per-node tracing is opt-in: it prints on every node and costs real time
 * on each inference, so it stays off unless FSV_NATIVE_TRACE is set.
 */
#define FSV_TRACE(...)                                                     \
    do {                                                                   \
        if (fsv_graph_trace_enabled()) std::fprintf(stderr, __VA_ARGS__);   \
    } while (0)

namespace {
bool fsv_graph_trace_enabled() {
    static const bool enabled = std::getenv("FSV_NATIVE_TRACE") != nullptr;
    return enabled;
}
}  // namespace

namespace fsv {

struct TensorDeviceStorage {
    unsigned long long pointer = 0;
    std::size_t bytes = 0;
    bool owned = false;        /* recycle the buffer when the last copy goes away */
    bool device_valid = false; /* the device buffer holds the current value */
    bool host_valid = false;   /* the host payload holds the current value */

    ~TensorDeviceStorage() {
        if (owned && pointer) fsv_cuda_device_free(pointer, bytes);
    }
};

}  // namespace fsv

#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <limits>
#include <map>
#include <numeric>
#include <stdexcept>

namespace {

using fsv::Tensor;
using fsv::TensorProto;
using fsv::Value;

bool cuda_host_enabled() {
    static const bool enabled = std::getenv("FSV_NATIVE_CPU_ONLY") == nullptr;
    /* A run that has run out of memory keeps every remaining node on the host:
       the card is not asked again until the next run resets the flag. */
    return enabled && !fsv_cuda_device_abandoned();
}

std::size_t element_size(int32_t dtype) {
    switch (dtype) {
        case 1: return 4;
        case 2: return 1;
        case 3: return 1;
        case 4: return 2;
        case 5: return 2;
        case 6: return 4;
        case 7: return 8;
        case 9: return 1;
        case 10: return 2;
        case 11: return 8;
        case 12: return 4;
        case 13: return 8;
        default: return 0;
    }
}

/* Temporary diagnostic (FSV_NATIVE_PROBE): splits the node loop's wall time
   into input gathering, operator execution and result storing, so an
   optimisation can be aimed at the part that actually costs. */
struct NodeProbe {
    unsigned long long nodes = 0;
    double gather = 0.0;
    double exec = 0.0;
    double store = 0.0;
};

NodeProbe& node_probe() {
    static NodeProbe* probe = new NodeProbe();
    return *probe;
}

bool node_probe_enabled() {
    static const bool enabled = std::getenv("FSV_NATIVE_PROBE") != nullptr;
    return enabled;
}

struct NodeProbeFlusher {
    ~NodeProbeFlusher() {
        if (!node_probe_enabled()) return;
        const NodeProbe& probe = node_probe();
        std::fprintf(stderr, "[probe] nodes=%llu gather=%.3fs exec=%.3fs store=%.3fs\n",
                     probe.nodes, probe.gather, probe.exec, probe.store);
    }
} g_node_probe_flusher;

using fsv::TensorDeviceStorage;

/* Makes the host payload current. Cheap for a host-only tensor, and a device
   tensor is downloaded at most once per device run: several CPU operators can
   then read the same bytes. */
void ensure_host(const Tensor& tensor) {
    TensorDeviceStorage* storage = tensor.device.get();
    if (!storage || storage->host_valid) return;
    const std::size_t bytes = tensor.byte_size();
    if (!bytes) return;
    Tensor& writable = const_cast<Tensor&>(tensor);
    if (writable.data.size() != bytes) writable.data.resize_shared(bytes);
    const int status = fsv_cuda_device_download(writable.data.data(), storage->pointer, bytes);
    storage->host_valid = true;
    if (status != 0) {
        std::fprintf(stderr, "[native-run] device download failed: %s\n",
                     fsv_cuda_last_error());
    }
}

/* Device view of a tensor, uploading it when the host payload is newer.
   Returns 0 when no device copy can be produced. */
fsv_cuda_ptr ensure_device(Tensor& tensor) {
    if (tensor.device && tensor.device->device_valid) return tensor.device->pointer;
    if (tensor.device && !tensor.device->owned) {
        /* A borrowed weight buffer must never be written through. */
        tensor.device.reset();
    }
    const std::size_t bytes = tensor.byte_size();
    if (!bytes) return 0;
    if (!tensor.device) {
        fsv_cuda_ptr resident = 0;
        if (tensor.data.size() == bytes &&
            fsv_cuda_constant_lookup(tensor.data.data(), bytes, &resident) == 0) {
            auto storage = std::make_shared<TensorDeviceStorage>();
            storage->pointer = resident;
            storage->bytes = bytes;
            storage->device_valid = true;
            storage->host_valid = true;
            tensor.device = storage;
            return resident;
        }
        fsv_cuda_ptr pointer = 0;
        if (fsv_cuda_device_alloc(bytes, &pointer) != 0) return 0;
        auto storage = std::make_shared<TensorDeviceStorage>();
        storage->pointer = pointer;
        storage->bytes = bytes;
        storage->owned = true;
        /* Only a host payload can seed the upload below. A result that never
           left the card has none, and claiming otherwise would send whatever
           the cleared buffer points at; leaving host_valid false makes the
           caller decline instead. "The host payload is authoritative" is also
           what keeps a later download from reading the un-initialised buffer
           back over the caller's data. */
        storage->host_valid = tensor.data.size() == bytes;
        tensor.device = storage;
    }
    if (!tensor.device->host_valid) return 0;
    if (fsv_cuda_device_upload(tensor.device->pointer, tensor.data.data(), bytes) != 0) return 0;
    tensor.device->device_valid = true;
    return tensor.device->pointer;
}

/* Reserves a fresh device buffer for a node result. */
bool adopt_device_output(Tensor& tensor) {
    const std::size_t bytes = tensor.byte_size();
    if (!bytes) return false;
    fsv_cuda_ptr pointer = 0;
    if (fsv_cuda_device_alloc(bytes, &pointer) != 0) return false;
    auto storage = std::make_shared<TensorDeviceStorage>();
    storage->pointer = pointer;
    storage->bytes = bytes;
    storage->owned = true;
    storage->device_valid = true;
    storage->host_valid = false;
    tensor.device = storage;
    /* Nothing on the host holds this value yet, and the buffer make_tensor
       allocated would only be zeroed and then thrown away. Clearing it means a
       device result costs host memory only when something actually reads the
       bytes back; the graph paid for 12 GB of short-lived host buffers a
       synthesis before this. The slot has to be the tensor's own: the bytes a
       CPU operator eventually reads back are written into it in place so every
       copy of this value sees them. */
    tensor.data.reset_unique();
    return true;
}

/* Counts every name a graph reads, subgraphs included. An If branch or a Loop
   body reads the enclosing scope directly, so those reads have to be charged
   to the outer graph or a value could be released while the branch still
   wants it. A Loop body runs an unknown number of times and its references are
   counted once, which can only keep a value alive too long. */
void count_value_uses(const fsv::GraphProto& graph,
                      std::unordered_map<std::string, std::size_t>& counts) {
    for (const auto& node : graph.nodes) {
        for (const std::string& name : node.inputs) {
            if (!name.empty()) ++counts[name];
        }
        for (const auto& attribute : node.attributes) {
            if (attribute.graph) count_value_uses(*attribute.graph, counts);
            for (const auto& nested : attribute.graphs) {
                if (nested) count_value_uses(*nested, counts);
            }
        }
    }
}

/* Gives a value's device buffer back once no later node reads it. A resident
   intermediate costs the same whether it is used or not, and a graph that
   keeps all of them exhausts a small card and then spends its time paging.
   Only the device copy is dropped: the release point is the last read, so the
   host payload the CPU operators would need is never asked for again. */
void release_device_copy(Tensor& tensor) {
    if (tensor.device) tensor.device.reset();
}

template <typename T>
T read_scalar(const Tensor& tensor, std::size_t index = 0) {
    ensure_host(tensor);
    T value{};
    if (tensor.data.size() >= (index + 1) * sizeof(T)) {
        std::memcpy(&value, tensor.data.data() + index * sizeof(T), sizeof(T));
    }
    return value;
}

template <typename T>
void write_scalar(Tensor& tensor, std::size_t index, T value) {
    if (tensor.data.size() >= (index + 1) * sizeof(T)) {
        if (tensor.device && !tensor.device->host_valid) ensure_host(tensor);
        if (tensor.device) tensor.device->device_valid = false;
        tensor.data.detach();
        std::memcpy(tensor.data.data() + index * sizeof(T), &value, sizeof(T));
    }
}

float half_to_float(std::uint16_t bits);

std::uint16_t float_to_half(float value) {
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    std::uint32_t sign = (bits >> 16) & 0x8000u;
    std::uint32_t exponent = (bits >> 23) & 0xffu;
    std::uint32_t mantissa = bits & 0x7fffffu;
    if (exponent == 0xffu) {
        if (mantissa == 0) return static_cast<std::uint16_t>(sign | 0x7c00u);
        return static_cast<std::uint16_t>(sign | 0x7c00u | (mantissa >> 13) | 0x200u);
    }
    int32_t half_exponent = static_cast<int32_t>(exponent) - 127 + 15;
    if (half_exponent >= 31) return static_cast<std::uint16_t>(sign | 0x7c00u);
    if (half_exponent <= 0) {
        if (half_exponent < -10) return static_cast<std::uint16_t>(sign);
        mantissa |= 0x800000u;
        std::uint32_t shift = static_cast<std::uint32_t>(14 - half_exponent);
        std::uint32_t rounded = mantissa >> shift;
        std::uint32_t remainder = mantissa & ((1u << shift) - 1u);
        if (remainder > (1u << (shift - 1u)) ||
            (remainder == (1u << (shift - 1u)) && (rounded & 1u))) {
            ++rounded;
        }
        return static_cast<std::uint16_t>(sign | rounded);
    }
    std::uint32_t rounded = mantissa >> 13;
    std::uint32_t remainder = mantissa & 0x1fffu;
    if (remainder > 0x1000u || (remainder == 0x1000u && (rounded & 1u))) {
        ++rounded;
        if (rounded == 0x400u) {
            rounded = 0;
            ++half_exponent;
            if (half_exponent >= 31) return static_cast<std::uint16_t>(sign | 0x7c00u);
        }
    }
    return static_cast<std::uint16_t>(sign |
        (static_cast<std::uint32_t>(half_exponent) << 10) | rounded);
}

double read_numeric(const Tensor& tensor, std::size_t index) {
    switch (tensor.dtype) {
        case 1: return read_scalar<float>(tensor, index);
        case 2: return read_scalar<std::uint8_t>(tensor, index);
        case 3: return read_scalar<std::int8_t>(tensor, index);
        case 4: return read_scalar<std::uint16_t>(tensor, index);
        case 5: return read_scalar<std::int16_t>(tensor, index);
        case 6: return read_scalar<std::int32_t>(tensor, index);
        case 7: return static_cast<double>(read_scalar<std::int64_t>(tensor, index));
        case 9: return read_scalar<std::uint8_t>(tensor, index) != 0;
        case 10: return half_to_float(read_scalar<std::uint16_t>(tensor, index));
        case 11: return read_scalar<double>(tensor, index);
        case 12: return read_scalar<std::uint32_t>(tensor, index);
        case 13: return static_cast<double>(read_scalar<std::uint64_t>(tensor, index));
        default: return 0.0;
    }
}

void write_numeric(Tensor& tensor, std::size_t index, double value) {
    switch (tensor.dtype) {
        case 1: write_scalar<float>(tensor, index, static_cast<float>(value)); break;
        case 2: write_scalar<std::uint8_t>(tensor, index, static_cast<std::uint8_t>(value)); break;
        case 3: write_scalar<std::int8_t>(tensor, index, static_cast<std::int8_t>(value)); break;
        case 4: write_scalar<std::uint16_t>(tensor, index, static_cast<std::uint16_t>(value)); break;
        case 5: write_scalar<std::int16_t>(tensor, index, static_cast<std::int16_t>(value)); break;
        case 6: write_scalar<std::int32_t>(tensor, index, static_cast<std::int32_t>(value)); break;
        case 7: write_scalar<std::int64_t>(tensor, index, static_cast<std::int64_t>(value)); break;
        case 9: write_scalar<std::uint8_t>(tensor, index, value != 0.0 ? 1 : 0); break;
        case 10: write_scalar<std::uint16_t>(tensor, index, float_to_half(static_cast<float>(value))); break;
        case 11: write_scalar<double>(tensor, index, value); break;
        case 12: write_scalar<std::uint32_t>(tensor, index, static_cast<std::uint32_t>(value)); break;
        case 13: write_scalar<std::uint64_t>(tensor, index, static_cast<std::uint64_t>(value)); break;
        default: break;
    }
}

std::vector<int64_t> strides_for(const std::vector<int64_t>& shape) {
    std::vector<int64_t> strides(shape.size(), 1);
    for (std::size_t index = shape.size(); index > 1; --index) {
        strides[index - 2] = strides[index - 1] * shape[index - 1];
    }
    return strides;
}

int64_t numel(const std::vector<int64_t>& shape) {
    int64_t total = 1;
    for (int64_t dim : shape) total *= dim;
    return total;
}

std::string shape_text(const std::vector<int64_t>& shape) {
    std::string value = "[";
    for (std::size_t index = 0; index < shape.size(); ++index) {
        if (index) value += ",";
        value += std::to_string(shape[index]);
    }
    value += "]";
    return value;
}

/* Temporary diagnostic (FSV_NATIVE_LAYOUT_DUMP): a histogram of the shapes the
   layout operators are asked to move, so the kernel rewrite is sized against
   the shapes a synthesis really produces. */
std::map<std::string, unsigned long long>& layout_shape_table() {
    static std::map<std::string, unsigned long long>* table =
        new std::map<std::string, unsigned long long>();
    return *table;
}

void layout_shape_note(const std::string& text) {
    static const bool enabled = std::getenv("FSV_NATIVE_LAYOUT_DUMP") != nullptr;
    if (!enabled) return;
    ++layout_shape_table()[text];
}

struct LayoutShapeFlusher {
    ~LayoutShapeFlusher() {
        if (!std::getenv("FSV_NATIVE_LAYOUT_DUMP")) return;
        std::vector<std::pair<unsigned long long, std::string>> rows;
        for (const auto& item : layout_shape_table()) {
            rows.emplace_back(item.second, item.first);
        }
        std::sort(rows.begin(), rows.end(),
                  [](const std::pair<unsigned long long, std::string>& left,
                     const std::pair<unsigned long long, std::string>& right) {
                      return left.first > right.first;
                  });
        for (const auto& row : rows) {
            std::fprintf(stderr, "[layout] %8llu %s\n", row.first, row.second.c_str());
        }
    }
} g_layout_shape_flusher;

Tensor make_tensor(const std::vector<int64_t>& shape, int32_t dtype) {
    Tensor output;
    output.shape = shape;
    output.dtype = dtype;
    output.data.resize(static_cast<std::size_t>(numel(shape)) * element_size(dtype));
    return output;
}

/* Shape and dtype without bytes of their own. An operator that aliases another
   tensor's payload, or hands the value to CUDA, has no use for the zeroed host
   buffer make_tensor would measure out: the graph threw away 12 GB of those a
   synthesis, and zeroing them cost more than the operators that made them. */
Tensor shape_only_tensor(const std::vector<int64_t>& shape, int32_t dtype) {
    Tensor output;
    output.shape = shape;
    output.dtype = dtype;
    return output;
}

/* Same, for a result CUDA writes into a device buffer. The empty payload has
   to be this tensor's own rather than the one every empty tensor shares, so
   that the bytes a CPU consumer later reads back land where every copy of the
   value can see them. */
Tensor device_tensor(const std::vector<int64_t>& shape, int32_t dtype) {
    Tensor output = shape_only_tensor(shape, dtype);
    output.data.reset_unique();
    return output;
}

Tensor scalar_tensor(int32_t dtype) {
    return make_tensor({}, dtype);
}

int64_t read_int_scalar(const Tensor& tensor, std::size_t index = 0) {
    if (tensor.dtype == 6) {
        return static_cast<int64_t>(read_scalar<int32_t>(tensor, index));
    }
    return read_scalar<int64_t>(tensor, index);
}

const fsv::AttributeProto* find_attr(const fsv::NodeProto& node, const std::string& name) {
    for (const auto& item : node.attributes) {
        if (item.name == name) return &item;
    }
    return nullptr;
}

int64_t attr_int(const fsv::NodeProto& node, const std::string& name, int64_t fallback = 0) {
    const fsv::AttributeProto* attr = find_attr(node, name);
    return attr ? attr->i : fallback;
}

Tensor tensor_from_proto(const TensorProto& proto) {
    Tensor output;
    output.shape = proto.dims;
    output.dtype = proto.data_type;
    output.data = proto.raw_data;
    return output;
}

std::string read_file(const std::string& path, std::string& error) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        error = "cannot open external weights: " + path;
        return {};
    }
    stream.seekg(0, std::ios::end);
    std::streamoff length = stream.tellg();
    if (length <= 0) {
        error = "empty external weights file: " + path;
        return {};
    }
    stream.seekg(0, std::ios::beg);
    std::string bytes(static_cast<std::size_t>(length), '\0');
    stream.read(bytes.data(), length);
    if (!stream) {
        error = "cannot read external weights file: " + path;
        return {};
    }
    return bytes;
}

bool parse_int64(const std::string& value, int64_t& output) {
    if (value.empty()) return false;
    char* end = nullptr;
    long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (end == value.c_str() || *end != '\0') return false;
    output = static_cast<int64_t>(parsed);
    return true;
}

/* A tier ships one external file per graph, and some of them store fp16 while
   the protobuf still describes the fp32 layout: every ``length`` is then twice
   the bytes on disk.  Which one a file is cannot be decided from a single
   tensor, because an fp32 file is large enough for either reading -- the
   fp16 test "offset/2 + length/2 fits" is true for an fp32 file as well, and
   reading the full-precision tiers that way turned their weights into noise.
   ``prepare`` therefore measures the file against the whole fp32 extent its
   metadata describes and passes the answer down: a file shorter than that
   extent is the packed one. */
bool load_external_weights(const std::string& file_data, const TensorProto& proto,
                           bool fp16_storage, Tensor& output, std::string& error) {
    if (file_data.empty()) {
        error = "missing external weights path for " + proto.name;
        return false;
    }
    int64_t offset = 0;
    int64_t length = 0;
    if (!proto.external_offset.empty() && !parse_int64(proto.external_offset, offset)) {
        error = "invalid external tensor offset: " + proto.external_offset;
        return false;
    }
    if (!proto.external_length.empty() && !parse_int64(proto.external_length, length)) {
        error = "invalid external tensor length: " + proto.external_length;
        return false;
    }
    const int64_t expected = numel(proto.dims) * static_cast<int64_t>(element_size(proto.data_type));
    if (length != expected) {
        error = "external tensor length mismatch for " + proto.name;
        return false;
    }
    /* Only the float tensors were packed; an index table in the same file keeps
       its own element size and its own offset. */
    const bool packed = fp16_storage && proto.data_type == 1;
    if (packed && (offset % 2 != 0 || length % 4 != 0)) {
        error = "packed external tensor is not aligned: " + proto.name;
        return false;
    }
    const std::size_t read_offset = packed ? static_cast<std::size_t>(offset) / 2
                                           : static_cast<std::size_t>(offset);
    const std::size_t read_bytes = packed ? static_cast<std::size_t>(length) / 2
                                          : static_cast<std::size_t>(length);
    if (offset < 0 || read_offset + read_bytes > file_data.size()) {
        error = "external tensor exceeds file for " + proto.name;
        return false;
    }
    output.shape = proto.dims;
    output.dtype = proto.data_type;
    output.data.resize(static_cast<std::size_t>(length));
    if (packed) {
        const auto* source = reinterpret_cast<const std::uint16_t*>(file_data.data() + read_offset);
        for (std::size_t index = 0; index < static_cast<std::size_t>(length) / 4; ++index) {
            const float value = half_to_float(source[index]);
            std::memcpy(output.data.data() + index * 4, &value, sizeof(value));
        }
        return true;
    }
    std::memcpy(output.data.data(), file_data.data() + read_offset, read_bytes);
    return true;
}

bool load_initializer(const TensorProto& proto, Tensor& output, std::string& error) {
    if (proto.data_location != 1) {
        if (!proto.has_raw_data) {
            error = "initializer without raw data is unsupported: " + proto.name;
            return false;
        }
        output = tensor_from_proto(proto);
        return true;
    }
    error = "external initializer must be loaded by prepare: " + proto.name;
    return false;
}

std::vector<int64_t> broadcast_shape(const Tensor& left, const Tensor& right) {
    std::size_t rank = std::max(left.shape.size(), right.shape.size());
    std::vector<int64_t> output(rank, 1);
    for (std::size_t index = 0; index < rank; ++index) {
        int64_t a = index < left.shape.size() ? left.shape[left.shape.size() - 1 - index] : 1;
        int64_t b = index < right.shape.size() ? right.shape[right.shape.size() - 1 - index] : 1;
        if (a != b && a != 1 && b != 1) return {};
        output[rank - 1 - index] = std::max(a, b);
    }
    return output;
}

Tensor broadcast_to(const Tensor& source, const std::vector<int64_t>& target_shape) {
    ensure_host(source);

    if (source.shape == target_shape) {
        Tensor output = shape_only_tensor(target_shape, source.dtype);
        output.data = source.data;
        output.device = source.device;
        return output;
    }
    Tensor output = make_tensor(target_shape, source.dtype);
    int64_t target_size = numel(target_shape);
    int64_t source_rank = static_cast<int64_t>(source.shape.size());
    int64_t target_rank = static_cast<int64_t>(target_shape.size());
    if (source_rank > target_rank) return output;
    std::size_t bytes = element_size(source.dtype);
    /* The source element index has to be built most-significant dim first: the
       obvious "multiply as you walk backwards" form silently transposes any
       trailing pair of non-unit dims (e.g. an attention mask added to scores). */
    std::vector<int64_t> coordinate(static_cast<std::size_t>(target_rank), 0);
    for (int64_t flat = 0; flat < target_size; ++flat) {
        int64_t source_index = 0;
        for (int64_t dim = 0; dim < target_rank; ++dim) {
            int64_t source_dim = dim - (target_rank - source_rank);
            if (source_dim < 0) continue;
            int64_t source_size = source.shape[static_cast<std::size_t>(source_dim)];
            source_index = source_index * source_size +
                (source_size == 1 ? 0 : coordinate[static_cast<std::size_t>(dim)]);
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    source.data.data() + static_cast<std::size_t>(source_index) * bytes, bytes);
        for (int64_t dim = target_rank - 1; dim >= 0; --dim) {
            if (++coordinate[static_cast<std::size_t>(dim)] < target_shape[static_cast<std::size_t>(dim)]) break;
            coordinate[static_cast<std::size_t>(dim)] = 0;
        }
    }
    return output;
}

Tensor reshape_tensor(const Tensor& source, const std::vector<int64_t>& requested) {
    std::vector<int64_t> target = requested;
    for (std::size_t index = 0; index < target.size(); ++index) {
        if (target[index] == 0) target[index] = index < source.shape.size() ? source.shape[index] : 1;
    }
    int64_t known = 1;
    int infer = -1;
    for (std::size_t index = 0; index < target.size(); ++index) {
        if (target[index] == -1) {
            if (infer >= 0) return {};
            infer = static_cast<int>(index);
        } else {
            known *= target[index];
        }
    }
    if (infer >= 0) target[static_cast<std::size_t>(infer)] = numel(source.shape) / known;
    Tensor output = shape_only_tensor(target, source.dtype);
    output.data = source.data;
    output.device = source.device;
    return output;
}

Tensor transpose_tensor(const Tensor& source, const std::vector<int64_t>& perm) {
    ensure_host(source);

    FSV_TRACE( "[native-run] transpose begin in=%s perm=%s\n",
                 shape_text(source.shape).c_str(), shape_text(perm).c_str());
    std::vector<int64_t> shape(source.shape.size());
    for (std::size_t index = 0; index < perm.size(); ++index) {
        shape[index] = source.shape[static_cast<std::size_t>(perm[index])];
    }
    Tensor output = make_tensor(shape, source.dtype);
    FSV_TRACE( "[native-run] transpose out=%s\n", shape_text(shape).c_str());
    std::vector<int64_t> source_strides = strides_for(source.shape);
    int64_t count = numel(shape);
    std::size_t bytes = element_size(source.dtype);
    for (int64_t flat = 0; flat < count; ++flat) {
        int64_t source_index = 0;
        int64_t current = flat;
        for (int64_t dim = static_cast<int64_t>(shape.size()) - 1; dim >= 0; --dim) {
            int64_t size = shape[static_cast<std::size_t>(dim)];
            int64_t coordinate = size ? current % size : 0;
            current = size ? current / size : 0;
            source_index += coordinate *
                source_strides[static_cast<std::size_t>(perm[static_cast<std::size_t>(dim)])];
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    source.data.data() + static_cast<std::size_t>(source_index) * bytes, bytes);
    }
    return output;
}

Tensor concat_tensors(const std::vector<Tensor>& items, int64_t axis) {    for (const Tensor& item : items) ensure_host(item);

    if (items.empty()) return {};
    std::vector<int64_t> shape = items[0].shape;
    if (axis < 0) axis += static_cast<int64_t>(shape.size());
    int64_t axis_size = 0;
    for (const Tensor& item : items) {
        if (item.dtype != items[0].dtype || item.shape.size() != shape.size()) return {};
        for (std::size_t index = 0; index < shape.size(); ++index) {
            if (index != static_cast<std::size_t>(axis) && item.shape[index] != shape[index]) return {};
        }
        axis_size += item.shape[static_cast<std::size_t>(axis)];
    }
    shape[static_cast<std::size_t>(axis)] = axis_size;
    Tensor output = make_tensor(shape, items[0].dtype);
    std::vector<int64_t> inner(shape.begin() + static_cast<std::size_t>(axis) + 1, shape.end());
    int64_t inner_size = numel(inner);
    int64_t outer_size = numel(std::vector<int64_t>(shape.begin(), shape.begin() + static_cast<std::size_t>(axis)));
    int64_t destination = 0;
    std::size_t bytes = element_size(items[0].dtype);
    for (const Tensor& item : items) {
        int64_t item_size = item.shape[static_cast<std::size_t>(axis)] * inner_size;
        for (int64_t outer_index = 0; outer_index < outer_size; ++outer_index) {
            std::memcpy(output.data.data() +
                            static_cast<std::size_t>(destination + outer_index * axis_size * inner_size) * bytes,
                        item.data.data() + static_cast<std::size_t>(outer_index * item_size) * bytes,
                        static_cast<std::size_t>(item_size) * bytes);
        }
        destination += item_size;
    }
    return output;
}

Tensor concat_shape_vector_tensors(const std::vector<Tensor>& items) {    for (const Tensor& item : items) ensure_host(item);

    if (items.empty()) return {};
    int32_t dtype = items[0].dtype;
    int64_t total = 0;
    for (const Tensor& item : items) {
        if (item.dtype != dtype || !item.dtype) return {};
        total += item.numel();
    }
    Tensor output = make_tensor({total}, dtype);
    std::size_t bytes = element_size(dtype);
    std::size_t destination = 0;
    for (const Tensor& item : items) {
        std::size_t item_bytes = static_cast<std::size_t>(item.numel()) * bytes;
        std::memcpy(output.data.data() + destination, item.data.data(), item_bytes);
        destination += item_bytes;
    }
    return output;
}

Tensor gather_tensor(const Tensor& data, const Tensor& indices, int64_t axis) {
    ensure_host(data);

    if (axis < 0) axis += static_cast<int64_t>(data.shape.size());
    if (axis < 0 || axis >= static_cast<int64_t>(data.shape.size())) return {};
    std::vector<int64_t> shape;
    shape.insert(shape.end(), data.shape.begin(), data.shape.begin() + static_cast<std::size_t>(axis));
    shape.insert(shape.end(), indices.shape.begin(), indices.shape.end());
    shape.insert(shape.end(), data.shape.begin() + static_cast<std::size_t>(axis) + 1, data.shape.end());
    Tensor output = make_tensor(shape, data.dtype);
    int64_t inner = numel(std::vector<int64_t>(
        data.shape.begin() + static_cast<std::size_t>(axis) + 1, data.shape.end()));
    int64_t outer = numel(std::vector<int64_t>(
        data.shape.begin(), data.shape.begin() + static_cast<std::size_t>(axis)));
    std::size_t bytes = element_size(data.dtype);
    for (int64_t o = 0; o < outer; ++o) {
        for (int64_t i = 0; i < indices.numel(); ++i) {
            int64_t index = read_int_scalar(indices, static_cast<std::size_t>(i));
            if (index < 0) index += data.shape[static_cast<std::size_t>(axis)];
            if (index < 0 || index >= data.shape[static_cast<std::size_t>(axis)]) return {};
            for (int64_t in = 0; in < inner; ++in) {
                int64_t source = o * data.shape[static_cast<std::size_t>(axis)] * inner + index * inner + in;
                int64_t dest = (o * indices.numel() + i) * inner + in;
                std::memcpy(output.data.data() + static_cast<std::size_t>(dest) * bytes,
                            data.data.data() + static_cast<std::size_t>(source) * bytes, bytes);
            }
        }
    }
    return output;
}

Tensor unsqueeze_tensor(const Tensor& source, const std::vector<int64_t>& axes) {
    std::vector<int64_t> shape = source.shape;
    for (int64_t axis : axes) {
        int64_t normalized = axis < 0 ? axis + static_cast<int64_t>(shape.size()) + 1 : axis;
        shape.insert(shape.begin() + normalized, 1);
    }
    Tensor output = shape_only_tensor(shape, source.dtype);
    output.data = source.data;
    output.device = source.device;
    return output;
}

Tensor argmax_tensor(const Tensor& source, int64_t axis, bool keepdims) {
    if (axis < 0) axis += static_cast<int64_t>(source.shape.size());
    if (axis < 0 || axis >= static_cast<int64_t>(source.shape.size())) return {};
    std::vector<int64_t> output_shape = source.shape;
    if (keepdims) output_shape[static_cast<std::size_t>(axis)] = 1;
    else output_shape.erase(output_shape.begin() + axis);
    Tensor output = make_tensor(output_shape, 7);
    int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + static_cast<std::size_t>(axis)));
    int64_t width = source.shape[static_cast<std::size_t>(axis)];
    int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + static_cast<std::size_t>(axis) + 1, source.shape.end()));
    for (int64_t o = 0; o < outer; ++o) {
        for (int64_t in = 0; in < inner; ++in) {
            float best = -std::numeric_limits<float>::infinity();
            int64_t best_index = 0;
            for (int64_t index = 0; index < width; ++index) {
                float value = static_cast<float>(read_numeric(source,
                    static_cast<std::size_t>((o * width + index) * inner + in)));
                if (value > best) {
                    best = value;
                    best_index = index;
                }
            }
            int64_t dest = keepdims ? (o * width + in) : (o * inner + in);
            write_scalar<int64_t>(output, static_cast<std::size_t>(dest), best_index);
        }
    }
    return output;
}

Tensor cast_tensor(const Tensor& source, int32_t target_dtype) {
    if (source.dtype == target_dtype) {
        Tensor output = shape_only_tensor(source.shape, target_dtype);
        output.data = source.data;
        output.device = source.device;
        return output;
    }
    Tensor output = make_tensor(source.shape, target_dtype);
    int64_t count = source.numel();
    for (int64_t index = 0; index < count; ++index) {
        write_numeric(output, static_cast<std::size_t>(index),
            read_numeric(source, static_cast<std::size_t>(index)));
    }
    return output;
}

Tensor binary_tensor(const Tensor& left, const Tensor& right, int operation) {
    std::vector<int64_t> shape = broadcast_shape(left, right);
    if (shape.empty() && !(left.shape.empty() && right.shape.empty())) return {};
    if (left.dtype == 0 || right.dtype == 0) return {};
    Tensor output = make_tensor(shape, left.dtype);
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    int64_t count = numel(shape);
    if (cuda_host_enabled() && a.dtype == 1 && b.dtype == 1 &&
        operation >= 0 && operation <= 2) {
        int status = fsv_cuda_binary_f32_host(
            reinterpret_cast<const float*>(a.data.data()),
            reinterpret_cast<const float*>(b.data.data()),
            reinterpret_cast<float*>(output.data.data()),
            static_cast<std::size_t>(count), operation);
        if (status == 0) return output;
    }
    for (int64_t index = 0; index < count; ++index) {
        double x = read_numeric(a, static_cast<std::size_t>(index));
        double y = read_numeric(b, static_cast<std::size_t>(index));
        double value = operation == 0 ? x + y : operation == 1 ? x * y :
            operation == 2 ? x - y : x / y;
        write_numeric(output, static_cast<std::size_t>(index), value);
    }
    return output;
}

Tensor unary_tensor(const Tensor& source, int operation, float alpha = 0.0f) {
    ensure_host(source);

    Tensor output = make_tensor(source.shape, source.dtype);
    if (source.dtype == 0) return {};
    int64_t count = source.numel();
    static const int cuda_operations[] = {10, 5, 6, 7, 3, 8, 4};
    static const int cuda_codes[] = {0, 1, 2, 3, 4, 5, 6};
    if (cuda_host_enabled() && source.dtype == 1 && count > 0) {
        for (std::size_t index = 0; index < sizeof(cuda_codes) / sizeof(cuda_codes[0]); ++index) {
            if (operation != cuda_operations[index]) continue;
            int status = fsv_cuda_unary_f32_host(
                reinterpret_cast<const float*>(source.data.data()),
                reinterpret_cast<float*>(output.data.data()),
                static_cast<std::size_t>(count), cuda_codes[index]);
            if (status == 0) return output;
            break;
        }
    }
    for (int64_t index = 0; index < count; ++index) {
        float x = static_cast<float>(read_numeric(source, static_cast<std::size_t>(index)));
        float value = 0.0f;
        if (operation == 0) value = -x;
        else if (operation == 1) value = std::sin(x);
        else if (operation == 2) value = std::cos(x);
        else if (operation == 3) value = std::sqrt(x);
        else if (operation == 4) value = std::erf(x);
        else if (operation == 5) value = std::tanh(x);
        else if (operation == 6) value = 1.0f / (1.0f + std::exp(-x));
        else if (operation == 7) value = std::exp(x);
        else if (operation == 8) value = std::log(x);
        else if (operation == 9) value = std::log1p(std::exp(x));
        else if (operation == 10) value = std::max(0.0f, x);
        else if (operation == 11) value = x >= 0.0f ? x : alpha * x;
        else if (operation == 12) value = std::floor(x);
        write_numeric(output, static_cast<std::size_t>(index), value);
    }
    return output;
}

Tensor pow_tensor(const Tensor& base, const Tensor& exponent) {
    std::vector<int64_t> shape = broadcast_shape(base, exponent);
    if (shape.empty() && !(base.shape.empty() && exponent.shape.empty())) return {};
    Tensor a = broadcast_to(base, shape);
    Tensor b = broadcast_to(exponent, shape);
    Tensor output = make_tensor(shape, base.dtype);
    if (base.dtype == 0 || exponent.dtype == 0) return {};
    int64_t count = numel(shape);
    for (int64_t index = 0; index < count; ++index) {
        double x = read_numeric(a, static_cast<std::size_t>(index));
        double y = read_numeric(b, static_cast<std::size_t>(index));
        write_numeric(output, static_cast<std::size_t>(index), std::pow(x, y));
    }
    return output;
}

std::vector<int64_t> broadcast_shape_vectors(const std::vector<int64_t>& left,
                                             const std::vector<int64_t>& right) {
    std::size_t rank = std::max(left.size(), right.size());
    std::vector<int64_t> output(rank, 1);
    for (std::size_t index = 0; index < rank; ++index) {
        int64_t a = index < left.size() ? left[left.size() - 1 - index] : 1;
        int64_t b = index < right.size() ? right[right.size() - 1 - index] : 1;
        if (a != b && a != 1 && b != 1) return {};
        output[rank - 1 - index] = std::max(a, b);
    }
    return output;
}

Tensor matmul_tensor(const Tensor& left, const Tensor& right) {
    ensure_host(left);
    ensure_host(right);

    if (left.dtype == 0 || right.dtype == 0) return {};
    if (left.shape.empty() || right.shape.empty()) return {};
    bool left_vec = left.shape.size() == 1;
    bool right_vec = right.shape.size() == 1;
    std::vector<int64_t> left_shape = left.shape;
    std::vector<int64_t> right_shape = right.shape;
    if (left_vec) left_shape.insert(left_shape.begin(), 1);
    if (right_vec) right_shape.push_back(1);
    if (left_shape.size() < 2 || right_shape.size() < 2) return {};
    int64_t m = left_shape[left_shape.size() - 2];
    int64_t k = left_shape.back();
    int64_t right_k = right_shape[right_shape.size() - 2];
    int64_t n = right_shape.back();
    if (k != right_k) return {};
    std::vector<int64_t> left_batch(left_shape.begin(), left_shape.end() - 2);
    std::vector<int64_t> right_batch(right_shape.begin(), right_shape.end() - 2);
    std::vector<int64_t> batch_shape = broadcast_shape_vectors(left_batch, right_batch);
    if (batch_shape.empty() && !(left_batch.empty() && right_batch.empty())) return {};
    int64_t batch_size = numel(batch_shape);
    std::vector<int64_t> output_shape = batch_shape;
    output_shape.push_back(m);
    output_shape.push_back(n);
    if (left_vec) output_shape.erase(output_shape.end() - 2);
    if (right_vec) output_shape.erase(output_shape.end() - 1);
    Tensor output = make_tensor(output_shape, left.dtype);
    if (output.numel() == 0) return output;
    std::vector<int64_t> batch_strides_out = strides_for(batch_shape);
    int64_t left_matrix_size = m * k;
    int64_t right_matrix_size = k * n;
    bool cuda_matmul = cuda_host_enabled() && left.dtype == 1 && right.dtype == 1 &&
        !left_vec && !right_vec;
    for (int64_t batch = 0; batch < batch_size; ++batch) {
        std::vector<int64_t> batch_coord(batch_shape.size());
        int64_t current = batch;
        for (int64_t dim = static_cast<int64_t>(batch_shape.size()) - 1; dim >= 0; --dim) {
            int64_t size = batch_shape[static_cast<std::size_t>(dim)];
            batch_coord[static_cast<std::size_t>(dim)] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        int64_t left_batch_index = 0;
        int64_t right_batch_index = 0;
        for (std::size_t dim = 0; dim < batch_shape.size(); ++dim) {
            int64_t coord = batch_coord[dim];
            int64_t left_dim = static_cast<int64_t>(dim) -
                (static_cast<int64_t>(batch_shape.size()) - static_cast<int64_t>(left_batch.size()));
            int64_t right_dim = static_cast<int64_t>(dim) -
                (static_cast<int64_t>(batch_shape.size()) - static_cast<int64_t>(right_batch.size()));
            if (left_dim >= 0 && left_batch[static_cast<std::size_t>(left_dim)] != 1) {
                left_batch_index += coord * left_matrix_size;
            }
            if (right_dim >= 0 && right_batch[static_cast<std::size_t>(right_dim)] != 1) {
                right_batch_index += coord * right_matrix_size;
            }
        }
        /* The strides above count matrices, the output is indexed in elements,
           so a batched MatMul has to scale by the size of one matrix. Without
           this every batch slice after the first lands on the wrong offset. */
        int64_t batch_output_index = 0;
        for (std::size_t dim = 0; dim < batch_shape.size(); ++dim) {
            batch_output_index += batch_coord[dim] *
                batch_strides_out[dim];
        }
        batch_output_index *= m * n;
        if (cuda_matmul) {
            int status = fsv_cuda_matmul_f32_host(
                reinterpret_cast<const float*>(left.data.data()) + left_batch_index,
                reinterpret_cast<const float*>(right.data.data()) + right_batch_index,
                reinterpret_cast<float*>(output.data.data()) + batch_output_index,
                static_cast<int>(m), static_cast<int>(k), static_cast<int>(n));
            if (status == 0) continue;
        }
        for (int64_t row = 0; row < m; ++row) {
            for (int64_t col = 0; col < n; ++col) {
                double total = 0.0;
                for (int64_t index = 0; index < k; ++index) {
                    double a = read_numeric(left, static_cast<std::size_t>(
                        left_batch_index + row * k + index));
                    double b = read_numeric(right, static_cast<std::size_t>(
                        right_batch_index + index * n + col));
                    total += a * b;
                }
                write_numeric(output, static_cast<std::size_t>(
                    batch_output_index + row * n + col), total);
            }
        }
    }
    if (left_vec || right_vec) {
        // The flat data layout is already correct after the shape squeeze above.
    }
    return output;
}

Tensor equal_tensor(const Tensor& left, const Tensor& right) {
    std::vector<int64_t> shape = broadcast_shape(left, right);
    if (shape.empty() && !(left.shape.empty() && right.shape.empty())) return {};
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    Tensor output = make_tensor(shape, 9);
    for (int64_t index = 0; index < output.numel(); ++index) {
        bool equal = read_numeric(a, static_cast<std::size_t>(index)) ==
            read_numeric(b, static_cast<std::size_t>(index));
        if (equal) write_scalar<std::uint8_t>(output, static_cast<std::size_t>(index), 1);
    }
    return output;
}

Tensor range_tensor(const Tensor& start_tensor, const Tensor& limit_tensor,
                    const Tensor& delta_tensor) {
    if (start_tensor.dtype != limit_tensor.dtype ||
        start_tensor.dtype != delta_tensor.dtype) return {};
    int32_t dtype = start_tensor.dtype;
    int64_t count = 0;
    if (dtype == 7) {
        int64_t start = read_scalar<int64_t>(start_tensor);
        int64_t limit = read_scalar<int64_t>(limit_tensor);
        int64_t delta = read_scalar<int64_t>(delta_tensor);
        if (delta == 0) return {};
        count = delta > 0 ? std::max<int64_t>(0, (limit - start + delta - 1) / delta) :
            std::max<int64_t>(0, (start - limit - delta - 1) / (-delta));
    } else if (dtype == 6) {
        int64_t start = read_scalar<int32_t>(start_tensor);
        int64_t limit = read_scalar<int32_t>(limit_tensor);
        int64_t delta = read_scalar<int32_t>(delta_tensor);
        if (delta == 0) return {};
        count = delta > 0 ? std::max<int64_t>(0, (limit - start + delta - 1) / delta) :
            std::max<int64_t>(0, (start - limit - delta - 1) / (-delta));
    } else if (dtype == 1) {
        float start = read_scalar<float>(start_tensor);
        float limit = read_scalar<float>(limit_tensor);
        float delta = read_scalar<float>(delta_tensor);
        if (delta == 0.0f) return {};
        count = static_cast<int64_t>(std::ceil(
            (limit - start) / delta));
        if (count < 0) count = 0;
    } else {
        return {};
    }
    Tensor output = make_tensor({count}, dtype);
    for (int64_t index = 0; index < count; ++index) {
        if (dtype == 7) {
            write_scalar<int64_t>(output, static_cast<std::size_t>(index),
                read_scalar<int64_t>(start_tensor) + index * read_scalar<int64_t>(delta_tensor));
        } else if (dtype == 6) {
            write_scalar<int32_t>(output, static_cast<std::size_t>(index),
                static_cast<int32_t>(read_scalar<int32_t>(start_tensor) +
                    index * read_scalar<int32_t>(delta_tensor)));
        } else {
            write_scalar<float>(output, static_cast<std::size_t>(index),
                read_scalar<float>(start_tensor) + static_cast<float>(index) *
                read_scalar<float>(delta_tensor));
        }
    }
    return output;
}

Tensor pad_tensor(const Tensor& source, const std::vector<int64_t>& pads,
                  float constant_value, int32_t dtype,
                  const std::string& mode) {
    ensure_host(source);

    if (source.shape.empty() || pads.size() != source.shape.size() * 2) return {};
    int64_t rank = static_cast<int64_t>(source.shape.size());
    std::vector<int64_t> output_shape = source.shape;
    for (int64_t dim = 0; dim < rank; ++dim) {
        int64_t begin = pads[static_cast<std::size_t>(dim)];
        int64_t end = pads[static_cast<std::size_t>(dim + rank)];
        if (begin < 0 || end < 0) return {};
        output_shape[static_cast<std::size_t>(dim)] += begin + end;
    }
    Tensor output = make_tensor(output_shape, dtype ? dtype : source.dtype);
    if (source.numel() == 0 || output.numel() == 0) return output;
    std::size_t bytes = element_size(source.dtype);
    std::vector<int64_t> output_strides = strides_for(output_shape);
    const std::vector<int64_t> source_strides = strides_for(source.shape);
    for (int64_t flat = 0; flat < output.numel(); ++flat) {
        int64_t current = flat;
        int64_t source_index = 0;
        bool valid = true;
        for (int64_t dim = rank - 1; dim >= 0; --dim) {
            int64_t size = output_shape[static_cast<std::size_t>(dim)];
            int64_t coord = size ? current % size : 0;
            current = size ? current / size : 0;
            int64_t source_coord = coord - pads[static_cast<std::size_t>(dim)];
            int64_t input_size = source.shape[static_cast<std::size_t>(dim)];
            if (source_coord < 0 || source_coord >= input_size) {
                if (mode == "edge") {
                    source_coord = std::max<int64_t>(0,
                        std::min<int64_t>(input_size - 1, source_coord));
                } else if (mode == "wrap") {
                    source_coord = ((source_coord % input_size) + input_size) % input_size;
                } else if (mode == "reflect" || mode == "symmetric") {
                    if (input_size > 1) {
                        int64_t period = mode == "reflect" ? 2 * input_size - 2
                                                           : 2 * input_size;
                        source_coord %= period;
                        if (source_coord < 0) source_coord += period;
                        if (mode == "reflect" && source_coord >= input_size) {
                            source_coord = period - source_coord;
                        } else if (mode == "symmetric" && source_coord >= input_size) {
                            source_coord = period - source_coord - 1;
                        }
                    } else {
                        source_coord = 0;
                    }
                } else {
                    valid = false;
                }
            }
            if (!valid) break;
            source_index += source_coord * source_strides[static_cast<std::size_t>(dim)];
        }
        if (!valid) {
            write_numeric(output, static_cast<std::size_t>(flat), constant_value);
            continue;
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    source.data.data() + static_cast<std::size_t>(source_index) * bytes,
                    bytes);
    }
    return output;
}

Tensor resize_axis_tensor(const Tensor& source, std::size_t axis,
                          int64_t output_size, double scale,
                          const std::string& mode,
                          const std::string& coordinate_mode,
                          const std::string& nearest_mode) {
    if (axis >= source.shape.size() || output_size < 0) return {};
    int64_t input_size = source.shape[axis];
    if (input_size < 0) return {};
    std::vector<int64_t> output_shape = source.shape;
    output_shape[axis] = output_size;
    Tensor output = make_tensor(output_shape, source.dtype);
    if (output.numel() == 0) return output;
    if (input_size == 0) return {};
    std::vector<int64_t> source_strides = strides_for(source.shape);
    std::vector<int64_t> coordinate(source.shape.size(), 0);
    std::size_t bytes = element_size(source.dtype);
    for (int64_t flat = 0; flat < output.numel(); ++flat) {
        int64_t current = flat;
        for (std::size_t dim = output_shape.size(); dim > 0; --dim) {
            int64_t size = output_shape[dim - 1];
            coordinate[dim - 1] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        int64_t coord = coordinate[axis];
        double position = 0.0;
        if (coordinate_mode == "align_corners") {
            position = input_size == 1 || output_size == 1 ? 0.0 :
                static_cast<double>(coord) * static_cast<double>(input_size - 1) /
                    static_cast<double>(std::max<int64_t>(1, output_size - 1));
        } else if (coordinate_mode == "half_pixel") {
            position = scale == 0.0 ? 0.0 :
                (static_cast<double>(coord) + 0.5) / scale - 0.5;
        } else if (coordinate_mode == "pytorch_half_pixel") {
            position = output_size == 1 ? 0.0 :
                (static_cast<double>(coord) + 0.5) / scale - 0.5;
        } else {
            position = scale == 0.0 ? 0.0 : static_cast<double>(coord) / scale;
        }
        if (mode == "nearest") {
            double lower_double = std::floor(position);
            int64_t lower = static_cast<int64_t>(lower_double);
            double fraction = position - lower_double;
            int64_t source_coord = lower;
            if (nearest_mode == "ceil") {
                source_coord = static_cast<int64_t>(std::ceil(position));
            } else if (nearest_mode == "round_prefer_ceil") {
                if (fraction >= 0.5) source_coord = lower + 1;
            } else if (nearest_mode == "round_prefer_floor") {
                if (fraction > 0.5) source_coord = lower + 1;
            }
            source_coord = std::max<int64_t>(0,
                std::min<int64_t>(input_size - 1, source_coord));
            int64_t source_flat = 0;
            for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
                int64_t point = dim == axis ? source_coord : coordinate[dim];
                source_flat += point * source_strides[dim];
            }
            std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                        source.data.data() + static_cast<std::size_t>(source_flat) * bytes,
                        bytes);
        } else if (mode == "linear") {
            double lower_double = std::floor(position);
            int64_t lower = static_cast<int64_t>(lower_double);
            double weight = position - lower_double;
            int64_t upper = lower + 1;
            lower = std::max<int64_t>(0, std::min<int64_t>(input_size - 1, lower));
            upper = std::max<int64_t>(0, std::min<int64_t>(input_size - 1, upper));
            int64_t left_flat = 0;
            int64_t right_flat = 0;
            for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
                int64_t left_point = dim == axis ? lower : coordinate[dim];
                int64_t right_point = dim == axis ? upper : coordinate[dim];
                left_flat += left_point * source_strides[dim];
                right_flat += right_point * source_strides[dim];
            }
            double left = read_numeric(source, static_cast<std::size_t>(left_flat));
            double right = read_numeric(source, static_cast<std::size_t>(right_flat));
            write_numeric(output, static_cast<std::size_t>(flat),
                          left * (1.0 - weight) + right * weight);
        } else {
            return {};
        }
    }
    return output;
}

Tensor resize_tensor(const Tensor& source, const std::vector<double>& scales,
                     const std::vector<int64_t>& sizes, const std::string& mode,
                     const std::string& coordinate_mode, const std::string& nearest_mode) {
    ensure_host(source);

    std::vector<int64_t> output_shape;
    if (!sizes.empty()) {
        output_shape = sizes;
    } else if (!scales.empty()) {
        for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
            double scale = dim < scales.size() ? scales[dim] : 1.0;
            output_shape.push_back(static_cast<int64_t>(std::floor(
                static_cast<double>(source.shape[dim]) * scale)));
        }
    } else {
        output_shape = source.shape;
    }
    if (output_shape.size() != source.shape.size()) return {};
    if (output_shape == source.shape) return source;
    Tensor result = source;
    for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
        int64_t output_size = output_shape[dim];
        if (output_size == result.shape[dim]) continue;
        double scale = 1.0;
        if (!sizes.empty()) {
            scale = result.shape[dim] == 0 ? 0.0 :
                static_cast<double>(output_size) / static_cast<double>(result.shape[dim]);
        } else if (dim < scales.size()) {
            scale = scales[dim];
        }
        result = resize_axis_tensor(result, dim, output_size, scale, mode,
                                    coordinate_mode, nearest_mode);
        if (!result.dtype) return {};
    }
    return result;
}

float half_to_float(std::uint16_t bits) {
    std::uint32_t sign = static_cast<std::uint32_t>(bits & 0x8000) << 16;
    std::uint32_t exponent = (bits >> 10) & 0x1f;
    std::uint32_t mantissa = bits & 0x03ff;
    std::uint32_t float_bits = 0;
    if (exponent == 0) {
        if (mantissa == 0) {
            float_bits = sign;
        } else {
            int shift = 0;
            while ((mantissa & 0x0400) == 0) {
                mantissa <<= 1;
                ++shift;
            }
            mantissa &= 0x03ff;
            float_bits = sign |
                (static_cast<std::uint32_t>(127 - 14 - shift) << 23) |
                (mantissa << 13);
        }
    } else if (exponent == 0x1f) {
        float_bits = sign | 0x7f800000u | (mantissa << 13);
    } else {
        float_bits = sign | ((exponent - 15 + 127) << 23) | (mantissa << 13);
    }
    float value = 0.0f;
    std::memcpy(&value, &float_bits, sizeof(value));
    return value;
}

Tensor reduce_tensor(const Tensor& source, const std::vector<int64_t>& raw_axes,
                     bool keepdims, int operation) {
    std::vector<int64_t> axes = raw_axes;
    for (int64_t& axis : axes) {
        if (axis < 0) axis += static_cast<int64_t>(source.shape.size());
    }
    std::sort(axes.begin(), axes.end());
    std::vector<int64_t> output_shape;
    for (std::size_t index = 0; index < source.shape.size(); ++index) {
        bool reduced = std::binary_search(axes.begin(), axes.end(), static_cast<int64_t>(index));
        if (!reduced || keepdims) output_shape.push_back(reduced ? 1 : source.shape[index]);
    }
    if (axes.empty()) {
        Tensor output = scalar_tensor(source.dtype);
        double total = 0.0;
        double maximum = -std::numeric_limits<double>::infinity();
        for (int64_t index = 0; index < source.numel(); ++index) {
            double value = read_numeric(source, static_cast<std::size_t>(index));
            if (operation == 2) maximum = std::max(maximum, value);
            else total += operation == 3 ? value * value : value;
        }
        if (operation == 1) total /= static_cast<double>(source.numel());
        else if (operation == 2) total = maximum;
        else if (operation == 3) total = std::sqrt(total);
        write_numeric(output, 0, total);
        return output;
    }
    Tensor output = make_tensor(output_shape, source.dtype);
    std::vector<int64_t> source_strides = strides_for(source.shape);
    std::vector<int64_t> reduced_dims;
    int64_t reduced_numel = 1;
    for (int64_t axis : axes) {
        reduced_dims.push_back(source.shape[static_cast<std::size_t>(axis)]);
        reduced_numel *= source.shape[static_cast<std::size_t>(axis)];
    }
    for (int64_t out_flat = 0; out_flat < output.numel(); ++out_flat) {
        std::vector<int64_t> out_coord(output_shape.size());
        int64_t current = out_flat;
        for (int64_t dim = static_cast<int64_t>(output_shape.size()) - 1; dim >= 0; --dim) {
            int64_t size = output_shape[static_cast<std::size_t>(dim)];
            out_coord[static_cast<std::size_t>(dim)] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        double total = 0.0;
        double maximum = -std::numeric_limits<double>::infinity();
        for (int64_t reduced_flat = 0; reduced_flat < reduced_numel; ++reduced_flat) {
            std::vector<int64_t> reduced_coord(reduced_dims.size());
            int64_t current_reduced = reduced_flat;
            for (int64_t dim = static_cast<int64_t>(reduced_dims.size()) - 1; dim >= 0; --dim) {
                int64_t size = reduced_dims[static_cast<std::size_t>(dim)];
                reduced_coord[static_cast<std::size_t>(dim)] = size ? current_reduced % size : 0;
                current_reduced = size ? current_reduced / size : 0;
            }
            std::size_t reduced_dim = 0;
            int64_t output_dim = 0;
            int64_t source_index = 0;
            for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
                bool reduced = std::binary_search(axes.begin(), axes.end(), static_cast<int64_t>(dim));
                if (reduced) {
                    source_index += reduced_coord[reduced_dim++] *
                        source_strides[dim];
                    if (keepdims) {
                        if (out_coord[static_cast<std::size_t>(output_dim)] != 0) return {};
                        ++output_dim;
                    }
                } else {
                    source_index += out_coord[static_cast<std::size_t>(output_dim)] *
                        source_strides[dim];
                    ++output_dim;
                }
            }
            double value = read_numeric(source, static_cast<std::size_t>(source_index));
            if (operation == 2) maximum = std::max(maximum, value);
            else total += operation == 3 ? value * value : value;
        }
        if (operation == 1) total /= static_cast<double>(reduced_numel);
        else if (operation == 2) total = maximum;
        else if (operation == 3) total = std::sqrt(total);
        write_numeric(output, static_cast<std::size_t>(out_flat), total);
    }
    return output;
}

std::vector<int64_t> ints_from_tensor(const Tensor& source) {
    std::vector<int64_t> values;
    values.reserve(static_cast<std::size_t>(source.numel()));
    for (int64_t index = 0; index < source.numel(); ++index) {
        values.push_back(read_int_scalar(source, static_cast<std::size_t>(index)));
    }
    return values;
}

Tensor slice_tensor(const Tensor& source, const std::vector<int64_t>& starts,
                    const std::vector<int64_t>& ends, const std::vector<int64_t>& axes,
                    const std::vector<int64_t>& steps) {
    ensure_host(source);

    if (source.shape.empty()) return {};
    int64_t rank = static_cast<int64_t>(source.shape.size());
    std::vector<int64_t> axes_out = axes;
    if (axes_out.empty()) {
        for (int64_t axis = 0; axis < rank; ++axis) axes_out.push_back(axis);
    }
    if (starts.size() > axes_out.size() || ends.size() > axes_out.size()) return {};
    if (!steps.empty() && steps.size() != axes_out.size()) return {};
    std::vector<int64_t> sliced_starts(axes_out.size(), 0);
    std::vector<int64_t> sliced_ends(axes_out.size(), 0);
    std::vector<int64_t> sliced_steps(axes_out.size(), 1);
    std::vector<int64_t> axis_map(static_cast<std::size_t>(rank), -1);
    std::vector<int64_t> output_shape = source.shape;
    for (std::size_t index = 0; index < axes_out.size(); ++index) {
        int64_t axis = axes_out[index];
        if (axis < 0) axis += rank;
        if (axis < 0 || axis >= rank) return {};
        if (!steps.empty()) sliced_steps[index] = steps[index];
        int64_t step = sliced_steps[index];
        int64_t dim = source.shape[static_cast<std::size_t>(axis)];
        int64_t start = index < starts.size() ? starts[index] : (step > 0 ? 0 : dim - 1);
        int64_t end = index < ends.size() ? ends[index] : (step > 0 ? dim : -1);
        if (step > 0) {
            if (start < 0) start += dim;
            if (end < 0) end += dim;
            start = std::max<int64_t>(0, std::min<int64_t>(dim, start));
            end = std::max<int64_t>(0, std::min<int64_t>(dim, end));
            sliced_starts[index] = start;
            sliced_ends[index] = end;
            output_shape[static_cast<std::size_t>(axis)] =
                start >= end ? 0 : (end - start + step - 1) / step;
        } else if (step < 0) {
            if (start < 0) start += dim;
            if (start >= dim) start = dim - 1;
            if (end < 0) end += dim;
            end = std::max<int64_t>(-1, std::min<int64_t>(dim - 1, end));
            sliced_starts[index] = start;
            sliced_ends[index] = end;
            output_shape[static_cast<std::size_t>(axis)] =
                start <= end ? 0 : (start - end - step - 1) / (-step);
        } else {
            return {};
        }
        axis_map[static_cast<std::size_t>(axis)] = static_cast<int64_t>(index);
    }
    Tensor output = make_tensor(output_shape, source.dtype);
    if (output.numel() == 0) return output;
    std::vector<int64_t> source_strides = strides_for(source.shape);
    std::size_t bytes = element_size(source.dtype);
    for (int64_t flat = 0; flat < output.numel(); ++flat) {
        std::vector<int64_t> output_coord(output_shape.size());
        int64_t current = flat;
        for (int64_t dim = static_cast<int64_t>(output_shape.size()) - 1; dim >= 0; --dim) {
            int64_t size = output_shape[static_cast<std::size_t>(dim)];
            output_coord[static_cast<std::size_t>(dim)] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        int64_t source_index = 0;
        for (int64_t dim = 0; dim < rank; ++dim) {
            int64_t coordinate = output_coord[static_cast<std::size_t>(dim)];
            int64_t sliced_axis = axis_map[static_cast<std::size_t>(dim)];
            if (sliced_axis >= 0) {
                coordinate = sliced_starts[static_cast<std::size_t>(sliced_axis)] +
                    coordinate * sliced_steps[static_cast<std::size_t>(sliced_axis)];
            }
            source_index += coordinate * source_strides[static_cast<std::size_t>(dim)];
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    source.data.data() + static_cast<std::size_t>(source_index) * bytes, bytes);
    }
    return output;
}

Tensor clip_tensor(const Tensor& source, const Tensor& minimum, const Tensor& maximum) {
    ensure_host(source);
    ensure_host(minimum);
    ensure_host(maximum);

    std::vector<int64_t> shape = source.shape;
    if (minimum.dtype) {
        shape = broadcast_shape_vectors(shape, minimum.shape);
    }
    if (maximum.dtype) {
        shape = broadcast_shape_vectors(shape, maximum.shape);
    }
    Tensor output = make_tensor(shape, source.dtype);
    Tensor a = broadcast_to(source, shape);
    Tensor lo = minimum.dtype ? broadcast_to(minimum, shape) : Tensor{};
    Tensor hi = maximum.dtype ? broadcast_to(maximum, shape) : Tensor{};
    for (int64_t index = 0; index < output.numel(); ++index) {
        double value = read_numeric(a, static_cast<std::size_t>(index));
        if (minimum.dtype) value = std::max(value,
            read_numeric(lo, static_cast<std::size_t>(index)));
        if (maximum.dtype) value = std::min(value,
            read_numeric(hi, static_cast<std::size_t>(index)));
        write_numeric(output, static_cast<std::size_t>(index), value);
    }
    return output;
}

Tensor compare_tensor(const Tensor& left, const Tensor& right, int operation) {
    std::vector<int64_t> shape = broadcast_shape(left, right);
    if (shape.empty() && !(left.shape.empty() && right.shape.empty())) return {};
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    Tensor output = make_tensor(shape, 9);
    for (int64_t index = 0; index < output.numel(); ++index) {
        bool value = false;
        double x = read_numeric(a, static_cast<std::size_t>(index));
        double y = read_numeric(b, static_cast<std::size_t>(index));
        if (operation == 0) value = x == y;
        else if (operation == 1) value = x < y;
        else if (operation == 2) value = x > y;
        if (value) write_scalar<std::uint8_t>(output, static_cast<std::size_t>(index), 1);
    }
    return output;
}

Tensor where_tensor(const Tensor& condition, const Tensor& left, const Tensor& right) {
    ensure_host(condition);
    ensure_host(left);
    ensure_host(right);

    std::vector<int64_t> shape = broadcast_shape_vectors(
        broadcast_shape_vectors(condition.shape, left.shape), right.shape);
    if (shape.empty() && !(condition.shape.empty() && left.shape.empty() && right.shape.empty())) return {};
    Tensor c = broadcast_to(condition, shape);
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    Tensor output = make_tensor(shape, left.dtype);
    for (int64_t index = 0; index < output.numel(); ++index) {
        bool condition_value = read_scalar<std::uint8_t>(c, static_cast<std::size_t>(index)) != 0;
        std::size_t source_index = static_cast<std::size_t>(index);
        std::size_t bytes = element_size(left.dtype);
        std::memcpy(output.data.data() + source_index * bytes,
                    (condition_value ? a : b).data.data() + source_index * bytes, bytes);
    }
    return output;
}

Tensor not_tensor(const Tensor& source) {
    Tensor output = make_tensor(source.shape, 9);
    for (int64_t index = 0; index < source.numel(); ++index) {
        bool value = read_scalar<std::uint8_t>(source, static_cast<std::size_t>(index)) == 0;
        if (value) write_scalar<std::uint8_t>(output, static_cast<std::size_t>(index), 1);
    }
    return output;
}

Tensor or_tensor(const Tensor& left, const Tensor& right) {
    std::vector<int64_t> shape = broadcast_shape(left, right);
    if (shape.empty() && !(left.shape.empty() && right.shape.empty())) return {};
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    Tensor output = make_tensor(shape, 9);
    for (int64_t index = 0; index < output.numel(); ++index) {
        bool value = read_scalar<std::uint8_t>(a, static_cast<std::size_t>(index)) ||
            read_scalar<std::uint8_t>(b, static_cast<std::size_t>(index));
        if (value) write_scalar<std::uint8_t>(output, static_cast<std::size_t>(index), 1);
    }
    return output;
}

Tensor max_binary_tensor(const Tensor& left, const Tensor& right) {
    std::vector<int64_t> shape = broadcast_shape(left, right);
    if (shape.empty() && !(left.shape.empty() && right.shape.empty())) return {};
    Tensor a = broadcast_to(left, shape);
    Tensor b = broadcast_to(right, shape);
    Tensor output = make_tensor(shape, left.dtype);
    for (int64_t index = 0; index < output.numel(); ++index) {
        double x = read_numeric(a, static_cast<std::size_t>(index));
        double y = read_numeric(b, static_cast<std::size_t>(index));
        write_numeric(output, static_cast<std::size_t>(index), std::max(x, y));
    }
    return output;
}

Tensor max_tensors(const std::vector<Tensor>& inputs) {
    if (inputs.empty()) return {};
    Tensor output = inputs.front();
    for (std::size_t index = 1; index < inputs.size(); ++index) {
        output = max_binary_tensor(output, inputs[index]);
        if (output.dtype == 0) return {};
    }
    return output;
}

Tensor expand_tensor(const Tensor& source, const std::vector<int64_t>& requested) {
    std::vector<int64_t> target = broadcast_shape_vectors(source.shape, requested);
    if (target.empty()) return {};
    return broadcast_to(source, target);
}

Tensor flatten_tensor(const Tensor& source, int64_t axis) {
    int64_t rank = static_cast<int64_t>(source.shape.size());
    if (axis < 0) axis += rank;
    if (axis < 0 || axis > rank) return {};
    int64_t first = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + axis));
    int64_t second = numel(std::vector<int64_t>(
        source.shape.begin() + axis, source.shape.end()));
    Tensor output = shape_only_tensor({first, second}, source.dtype);
    output.data = source.data;
    output.device = source.device;
    return output;
}

Tensor prelu_tensor(const Tensor& source, const Tensor& slope) {
    std::vector<int64_t> shape = broadcast_shape(source, slope);
    if (shape.empty() && !(source.shape.empty() && slope.shape.empty())) return {};
    Tensor x = broadcast_to(source, shape);
    Tensor s = broadcast_to(slope, shape);
    Tensor output = make_tensor(shape, source.dtype);
    for (int64_t index = 0; index < output.numel(); ++index) {
        double value = read_numeric(x, static_cast<std::size_t>(index));
        double slope_value = read_numeric(s, static_cast<std::size_t>(index));
        write_numeric(output, static_cast<std::size_t>(index),
            value >= 0.0 ? value : value * slope_value);
    }
    return output;
}

Tensor tile_tensor(const Tensor& source, const std::vector<int64_t>& repeats) {
    ensure_host(source);

    if (repeats.size() != source.shape.size()) return {};
    std::vector<int64_t> output_shape(source.shape.size());
    for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
        if (repeats[dim] < 0) return {};
        output_shape[dim] = source.shape[dim] * repeats[dim];
    }
    Tensor output = make_tensor(output_shape, source.dtype);
    if (output.numel() == 0) return output;
    std::vector<int64_t> source_strides = strides_for(source.shape);
    std::size_t bytes = element_size(source.dtype);
    for (int64_t flat = 0; flat < output.numel(); ++flat) {
        int64_t current = flat;
        int64_t source_index = 0;
        for (int64_t dim = static_cast<int64_t>(output_shape.size()) - 1; dim >= 0; --dim) {
            int64_t size = output_shape[static_cast<std::size_t>(dim)];
            int64_t coord = size ? current % size : 0;
            current = size ? current / size : 0;
            int64_t source_dim = source.shape[static_cast<std::size_t>(dim)];
            if (source_dim) source_index += (coord % source_dim) * source_strides[static_cast<std::size_t>(dim)];
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    source.data.data() + static_cast<std::size_t>(source_index) * bytes, bytes);
    }
    return output;
}

Tensor gather_elements_tensor(const Tensor& data, const Tensor& indices, int64_t axis) {
    ensure_host(data);

    if (data.shape.size() != indices.shape.size()) return {};
    int64_t rank = static_cast<int64_t>(data.shape.size());
    if (rank == 0) return {};
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank) return {};
    Tensor output = make_tensor(indices.shape, data.dtype);
    std::vector<int64_t> data_strides = strides_for(data.shape);
    std::size_t bytes = element_size(data.dtype);
    for (int64_t flat = 0; flat < output.numel(); ++flat) {
        int64_t current = flat;
        std::vector<int64_t> coord(static_cast<std::size_t>(rank));
        for (int64_t dim = rank - 1; dim >= 0; --dim) {
            int64_t size = indices.shape[static_cast<std::size_t>(dim)];
            coord[static_cast<std::size_t>(dim)] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        int64_t index = read_int_scalar(indices, static_cast<std::size_t>(flat));
        if (index < 0) index += data.shape[static_cast<std::size_t>(axis)];
        if (index < 0 || index >= data.shape[static_cast<std::size_t>(axis)]) return {};
        coord[static_cast<std::size_t>(axis)] = index;
        int64_t source = 0;
        for (int64_t dim = 0; dim < rank; ++dim) {
            source += coord[static_cast<std::size_t>(dim)] *
                data_strides[static_cast<std::size_t>(dim)];
        }
        std::memcpy(output.data.data() + static_cast<std::size_t>(flat) * bytes,
                    data.data.data() + static_cast<std::size_t>(source) * bytes, bytes);
    }
    return output;
}

Tensor scatter_elements_tensor(const Tensor& data, const Tensor& indices,
                               const Tensor& updates, int64_t axis,
                               const std::string& reduction) {
    ensure_host(data);
    ensure_host(updates);

    int64_t rank = static_cast<int64_t>(data.shape.size());
    if (rank == 0 || indices.shape.size() != data.shape.size() ||
        updates.shape.size() != data.shape.size()) return {};
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank) return {};
    for (int64_t dim = 0; dim < rank; ++dim) {
        if (dim == axis) continue;
        if (data.shape[static_cast<std::size_t>(dim)] !=
                indices.shape[static_cast<std::size_t>(dim)] ||
            data.shape[static_cast<std::size_t>(dim)] !=
                updates.shape[static_cast<std::size_t>(dim)]) {
            return {};
        }
    }
    /* ScatterElements is "the operand with a few positions overwritten", so the
       result starts as a copy of it. Both halves of that copy have to be
       private: the payload, because writing the scattered values must not edit
       the operand, and the device handle, because it describes the operand and
       not the result. Sharing the handle left a tensor whose host bytes and
       device bytes disagreed while both claimed to be current, and the next
       CUDA operator to read it picked up the untouched input. */
    Tensor output = data;
    output.data.detach();
    output.device.reset();
    std::vector<int64_t> data_strides = strides_for(data.shape);
    std::size_t bytes = element_size(data.dtype);
    for (int64_t flat = 0; flat < indices.numel(); ++flat) {
        int64_t current = flat;
        std::vector<int64_t> coord(static_cast<std::size_t>(rank));
        for (int64_t dim = rank - 1; dim >= 0; --dim) {
            int64_t size = indices.shape[static_cast<std::size_t>(dim)];
            coord[static_cast<std::size_t>(dim)] = size ? current % size : 0;
            current = size ? current / size : 0;
        }
        int64_t index = read_int_scalar(indices, static_cast<std::size_t>(flat));
        if (index < 0) index += data.shape[static_cast<std::size_t>(axis)];
        if (index < 0 || index >= data.shape[static_cast<std::size_t>(axis)]) return {};
        coord[static_cast<std::size_t>(axis)] = index;
        int64_t destination = 0;
        for (int64_t dim = 0; dim < rank; ++dim) {
            destination += coord[static_cast<std::size_t>(dim)] *
                data_strides[static_cast<std::size_t>(dim)];
        }
        if (reduction.empty() || reduction == "none") {
            std::memcpy(output.data.data() + static_cast<std::size_t>(destination) * bytes,
                        updates.data.data() + static_cast<std::size_t>(flat) * bytes, bytes);
        } else {
            double current_value = read_numeric(output, static_cast<std::size_t>(destination));
            double update_value = read_numeric(updates, static_cast<std::size_t>(flat));
            double value = current_value;
            if (reduction == "add") value = current_value + update_value;
            else if (reduction == "mul") value = current_value * update_value;
            else if (reduction == "min") value = std::min(current_value, update_value);
            else if (reduction == "max") value = std::max(current_value, update_value);
            else return {};
            write_numeric(output, static_cast<std::size_t>(destination), value);
        }
    }
    return output;
}

std::vector<Tensor> split_tensors(const Tensor& source,
                                  const std::vector<int64_t>& split, int64_t axis) {
    ensure_host(source);

    int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank == 0 || axis < 0 || axis >= rank) return {};
    if (split.empty()) return {};
    int64_t total = 0;
    for (int64_t value : split) total += value;
    if (total != source.shape[static_cast<std::size_t>(axis)]) return {};
    std::vector<Tensor> outputs;
    outputs.reserve(split.size());
    int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + static_cast<std::size_t>(axis) + 1, source.shape.end()));
    int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + static_cast<std::size_t>(axis)));
    std::size_t bytes = element_size(source.dtype);
    int64_t start = 0;
    for (int64_t size : split) {
        std::vector<int64_t> shape = source.shape;
        shape[static_cast<std::size_t>(axis)] = size;
        Tensor part = make_tensor(shape, source.dtype);
        for (int64_t o = 0; o < outer; ++o) {
            std::memcpy(part.data.data() +
                            static_cast<std::size_t>(o * size * inner) * bytes,
                        source.data.data() +
                            static_cast<std::size_t>((o * source.shape[static_cast<std::size_t>(axis)] + start) *
                                inner) * bytes,
                        static_cast<std::size_t>(size * inner) * bytes);
        }
        outputs.push_back(std::move(part));
        start += size;
    }
    return outputs;
}

std::vector<Tensor> topk_tensors(const Tensor& source, int64_t k, int64_t axis,
                                 bool largest, bool sorted) {
    int64_t rank = static_cast<int64_t>(source.shape.size());
    if (axis < 0) axis += rank;
    if (rank == 0 || axis < 0 || axis >= rank) return {};
    if (k < 0 || k > source.shape[static_cast<std::size_t>(axis)]) return {};
    int64_t width = source.shape[static_cast<std::size_t>(axis)];
    int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + static_cast<std::size_t>(axis)));
    int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + static_cast<std::size_t>(axis) + 1, source.shape.end()));
    std::vector<int64_t> shape = source.shape;
    shape[static_cast<std::size_t>(axis)] = k;
    Tensor values = make_tensor(shape, source.dtype);
    Tensor indices = make_tensor(shape, 7);
    for (int64_t o = 0; o < outer; ++o) {
        for (int64_t i = 0; i < inner; ++i) {
            std::vector<std::pair<double, int64_t>> candidates;
            candidates.reserve(static_cast<std::size_t>(width));
            for (int64_t w = 0; w < width; ++w) {
                candidates.emplace_back(read_numeric(source,
                    static_cast<std::size_t>((o * width + w) * inner + i)), w);
            }
            std::sort(candidates.begin(), candidates.end(),
                [largest, sorted](const std::pair<double, int64_t>& a,
                                  const std::pair<double, int64_t>& b) {
                    if (largest) {
                        return a.first != b.first ? a.first > b.first : a.second < b.second;
                    }
                    return a.first != b.first ? a.first < b.first : a.second < b.second;
                });
            for (int64_t w = 0; w < k; ++w) {
                std::size_t destination = static_cast<std::size_t>((o * k + w) * inner + i);
                write_numeric(values, destination, candidates[static_cast<std::size_t>(w)].first);
                write_scalar<int64_t>(indices, destination, candidates[static_cast<std::size_t>(w)].second);
            }
        }
    }
    if (!sorted) {
        // The graph only relies on selected elements; keep deterministic source order.
    }
    return {std::move(values), std::move(indices)};
}

Tensor squeeze_axis_tensor(const Tensor& source, int64_t axis) {
    std::vector<int64_t> shape = source.shape;
    if (shape[static_cast<std::size_t>(axis)] != 1) return {};
    shape.erase(shape.begin() + axis);
    Tensor output = shape_only_tensor(shape, source.dtype);
    output.data = source.data;
    output.device = source.device;
    return output;
}

std::vector<Value> split_to_sequence_values(const Tensor& source,
                                            const std::vector<int64_t>& split,
                                            int64_t axis, bool keepdims) {
    std::vector<Tensor> parts = split_tensors(source, split, axis);
    std::vector<Value> sequence;
    sequence.reserve(parts.size());
    for (Tensor& part : parts) {
        Value value;
        if (!keepdims && part.shape[static_cast<std::size_t>(axis)] == 1) {
            value.tensor = std::make_shared<Tensor>(squeeze_axis_tensor(part, axis));
        } else {
            value.tensor = std::make_shared<Tensor>(std::move(part));
        }
        sequence.push_back(std::move(value));
    }
    return sequence;
}

Tensor concat_from_sequence_values(const std::vector<Value>& sequence,
                                   int64_t axis, bool new_axis,
                                   int32_t empty_dtype) {
    std::vector<Tensor> items;
    items.reserve(sequence.size());
    for (const Value& value : sequence) {
        if (!value.tensor) return {};
        Tensor item = *value.tensor;
        if (new_axis) {
            std::vector<int64_t> shape = item.shape;
            if (axis < 0) axis += static_cast<int64_t>(shape.size()) + 1;
            if (axis < 0 || axis > static_cast<int64_t>(shape.size())) return {};
            Tensor expanded = shape_only_tensor(shape, item.dtype);
            expanded.data = item.data;
            shape.insert(shape.begin() + axis, 1);
            expanded.shape = shape;
            item = std::move(expanded);
        }
        items.push_back(std::move(item));
    }
    if (items.empty()) {
        int32_t dtype = empty_dtype ? empty_dtype : 1;
        if (axis < 0) axis = 0;
        std::vector<int64_t> shape(static_cast<std::size_t>(axis) + 1, 0);
        return make_tensor(shape, dtype);
    }
    return concat_tensors(items, axis);
}

Value sequence_insert_value(const Value& sequence, const Tensor& tensor,
                            int64_t position) {
    Value output;
    output.is_sequence = true;
    output.sequence = sequence.sequence;
    output.sequence_dtype = sequence.sequence_dtype ? sequence.sequence_dtype : tensor.dtype;
    Value item;
    item.tensor = std::make_shared<Tensor>(tensor);
    if (position < 0) position += static_cast<int64_t>(output.sequence.size()) + 1;
    if (position < 0 || position > static_cast<int64_t>(output.sequence.size())) return {};
    output.sequence.insert(output.sequence.begin() + position, item);
    return output;
}

Tensor sequence_at_tensor(const Value& sequence, int64_t position) {
    if (position < 0) position += static_cast<int64_t>(sequence.sequence.size());
    if (position < 0 || position >= static_cast<int64_t>(sequence.sequence.size())) return {};
    if (!sequence.sequence[static_cast<std::size_t>(position)].tensor) return {};
    return *sequence.sequence[static_cast<std::size_t>(position)].tensor;
}

template <typename T>
Tensor cumsum_tensor(const Tensor& source, int64_t axis, bool exclusive, bool reverse) {
    int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + static_cast<std::size_t>(axis)));
    int64_t width = source.shape[static_cast<std::size_t>(axis)];
    int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + static_cast<std::size_t>(axis) + 1, source.shape.end()));
    Tensor output = make_tensor(source.shape, source.dtype);
    for (int64_t o = 0; o < outer; ++o) {
        for (int64_t i = 0; i < inner; ++i) {
            T total = static_cast<T>(0);
            for (int64_t step = 0; step < width; ++step) {
                int64_t position = reverse ? width - 1 - step : step;
                std::size_t source_index = static_cast<std::size_t>(
                    (o * width + position) * inner + i);
                std::size_t output_index = source_index;
                if (exclusive) {
                    write_scalar<T>(output, output_index, total);
                    total += read_scalar<T>(source, source_index);
                } else {
                    total += read_scalar<T>(source, source_index);
                    write_scalar<T>(output, output_index, total);
                }
            }
        }
    }
    return output;
}

Tensor squeeze_tensor(const Tensor& source, const std::vector<int64_t>& raw_axes) {
    std::vector<int64_t> axes;
    for (int64_t axis : raw_axes) {
        if (axis < 0) axis += static_cast<int64_t>(source.shape.size());
        if (axis < 0 || axis >= static_cast<int64_t>(source.shape.size())) return {};
        axes.push_back(axis);
    }
    std::vector<int64_t> output_shape;
    if (axes.empty()) {
        for (int64_t dim : source.shape) {
            if (dim != 1) output_shape.push_back(dim);
        }
    } else {
        for (std::size_t dim = 0; dim < source.shape.size(); ++dim) {
            bool remove = std::find(axes.begin(), axes.end(), static_cast<int64_t>(dim)) != axes.end();
            if (!remove) {
                output_shape.push_back(source.shape[dim]);
            } else if (source.shape[dim] != 1) {
                return {};
            }
        }
    }
    Tensor output = shape_only_tensor(output_shape, source.dtype);
    output.data = source.data;
    output.device = source.device;
    return output;
}

Tensor instance_normalization_tensor(const Tensor& input, const Tensor& scale,
                                     const Tensor& bias, float epsilon) {
    ensure_host(input);

    if (input.shape.size() < 3) return {};
    int64_t batch = input.shape[0];
    int64_t channels = input.shape[1];
    int64_t spatial = numel(std::vector<int64_t>(
        input.shape.begin() + 2, input.shape.end()));
    Tensor output = make_tensor(input.shape, input.dtype);
    for (int64_t b = 0; b < batch; ++b) {
        for (int64_t c = 0; c < channels; ++c) {
            float mean = 0.0f;
            float square_sum = 0.0f;
            for (int64_t s = 0; s < spatial; ++s) {
                float value = static_cast<float>(read_numeric(input,
                    static_cast<std::size_t>(((b * channels + c) * spatial) + s)));
                mean += value;
                square_sum += value * value;
            }
            mean /= static_cast<float>(spatial);
            float variance = square_sum / static_cast<float>(spatial) - mean * mean;
            float scale_value = static_cast<float>(read_numeric(scale, static_cast<std::size_t>(c)));
            float bias_value = static_cast<float>(read_numeric(bias, static_cast<std::size_t>(c)));
            float inv = 1.0f / std::sqrt(variance + epsilon);
            for (int64_t s = 0; s < spatial; ++s) {
                std::size_t index = static_cast<std::size_t>(((b * channels + c) * spatial) + s);
                float value = static_cast<float>(read_numeric(input, index));
                write_numeric(output, index,
                    (value - mean) * inv * scale_value + bias_value);
            }
        }
    }
    return output;
}

Tensor softmax_tensor(const Tensor& source, int64_t axis) {
    ensure_host(source);

    if (source.dtype == 0) return {};
    int64_t rank = static_cast<int64_t>(source.shape.size());
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank) return {};
    int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + axis));
    int64_t width = source.shape[static_cast<std::size_t>(axis)];
    int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + axis + 1, source.shape.end()));
    Tensor output = make_tensor(source.shape, source.dtype);
    if (cuda_host_enabled() && source.dtype == 1 && inner == 1 &&
        outer > 0 && width > 0) {
        int status = fsv_cuda_softmax_f32_host(
            reinterpret_cast<const float*>(source.data.data()),
            reinterpret_cast<float*>(output.data.data()),
            static_cast<int>(outer), static_cast<int>(width));
        if (status == 0) return output;
    }
    for (int64_t o = 0; o < outer; ++o) {
        for (int64_t i = 0; i < inner; ++i) {
            double maximum = -std::numeric_limits<double>::infinity();
            for (int64_t w = 0; w < width; ++w) {
                maximum = std::max(maximum, read_numeric(source,
                    static_cast<std::size_t>((o * width + w) * inner + i)));
            }
            double total = 0.0;
            for (int64_t w = 0; w < width; ++w) {
                double value = std::exp(read_numeric(source,
                    static_cast<std::size_t>((o * width + w) * inner + i)) - maximum);
                total += value;
                write_numeric(output, static_cast<std::size_t>(
                    (o * width + w) * inner + i), value);
            }
            for (int64_t w = 0; w < width; ++w) {
                std::size_t index = static_cast<std::size_t>(
                    (o * width + w) * inner + i);
                write_numeric(output, index,
                    read_numeric(output, index) / total);
            }
        }
    }
    return output;
}

Tensor layer_normalization_tensor(const Tensor& input, const Tensor& scale,
                                  const Tensor& bias, int64_t axis, float epsilon) {
    ensure_host(input);
    ensure_host(scale);
    ensure_host(bias);

    if (input.dtype == 0) return {};
    int64_t rank = static_cast<int64_t>(input.shape.size());
    if (axis < 0) axis += rank;
    if (axis < 0 || axis > rank) return {};
    int64_t rows = numel(std::vector<int64_t>(
        input.shape.begin(), input.shape.begin() + axis));
    int64_t width = numel(std::vector<int64_t>(
        input.shape.begin() + axis, input.shape.end()));
    Tensor output = make_tensor(input.shape, input.dtype);
    if (cuda_host_enabled() && input.dtype == 1 && scale.dtype == 1 &&
        (bias.dtype == 0 || bias.dtype == 1) && rows > 0 && width > 0) {
        const float* bias_data = bias.dtype
            ? reinterpret_cast<const float*>(bias.data.data()) : nullptr;
        int status = fsv_cuda_layernorm_f32_host(
            reinterpret_cast<const float*>(input.data.data()),
            reinterpret_cast<const float*>(scale.data.data()),
            bias_data,
            reinterpret_cast<float*>(output.data.data()),
            static_cast<int>(rows), static_cast<int>(width), epsilon);
        if (status == 0) return output;
    }
    for (int64_t row = 0; row < rows; ++row) {
        double mean = 0.0;
        double square_sum = 0.0;
        for (int64_t index = 0; index < width; ++index) {
            double value = read_numeric(input,
                static_cast<std::size_t>(row * width + index));
            mean += value;
            square_sum += value * value;
        }
        mean /= static_cast<double>(width);
        double variance = std::max(0.0,
            square_sum / static_cast<double>(width) - mean * mean);
        double inv = 1.0 / std::sqrt(variance + epsilon);
        for (int64_t index = 0; index < width; ++index) {
            double value = read_numeric(input,
                static_cast<std::size_t>(row * width + index));
            double scale_value = read_numeric(scale, static_cast<std::size_t>(index));
            double bias_value = read_numeric(bias, static_cast<std::size_t>(index));
            write_numeric(output, static_cast<std::size_t>(
                row * width + index), (value - mean) * inv * scale_value + bias_value);
        }
    }
    return output;
}

Tensor batch_normalization_tensor(const Tensor& input, const Tensor& scale,
                                  const Tensor& bias, const Tensor& mean,
                                  const Tensor& variance, float epsilon) {
    if (input.shape.size() < 2 || input.dtype == 0) return {};
    int64_t batch = input.shape[0];
    int64_t channels = input.shape[1];
    int64_t spatial = numel(std::vector<int64_t>(
        input.shape.begin() + 2, input.shape.end()));
    Tensor output = make_tensor(input.shape, input.dtype);
    for (int64_t c = 0; c < channels; ++c) {
        double mean_value = read_numeric(mean, static_cast<std::size_t>(c));
        double var_value = read_numeric(variance, static_cast<std::size_t>(c));
        double inv = 1.0 / std::sqrt(var_value + epsilon);
        double scale_value = read_numeric(scale, static_cast<std::size_t>(c));
        double bias_value = read_numeric(bias, static_cast<std::size_t>(c));
        for (int64_t n = 0; n < batch; ++n) {
            for (int64_t s = 0; s < spatial; ++s) {
                std::size_t index = static_cast<std::size_t>(
                    (n * channels + c) * spatial + s);
                double value = read_numeric(input, index);
                write_numeric(output, index,
                    (value - mean_value) * inv * scale_value + bias_value);
            }
        }
    }
    return output;
}

Tensor gemm_tensor(const Tensor& left, const Tensor& right, const Tensor* bias,
                   float alpha, float beta, bool transpose_a, bool transpose_b) {
    ensure_host(left);
    ensure_host(right);
    if (bias) ensure_host(*bias);

    if (left.shape.size() != 2 || right.shape.size() != 2 ||
        left.dtype == 0 || right.dtype == 0) return {};
    int64_t m = transpose_a ? left.shape[1] : left.shape[0];
    int64_t k = transpose_a ? left.shape[0] : left.shape[1];
    int64_t right_k = transpose_b ? right.shape[1] : right.shape[0];
    int64_t n = transpose_b ? right.shape[0] : right.shape[1];
    if (k != right_k) return {};
    Tensor output = make_tensor({m, n}, left.dtype);
    if (cuda_host_enabled() && left.dtype == 1 && right.dtype == 1 &&
        (!bias || bias->dtype == 1)) {
        /* The kernel reads plain row-major operands, so a transposed input is
           materialised first: copying a 512x512 weight costs a fraction of the
           product itself. */
        Tensor a = transpose_a ? transpose_tensor(left, {1, 0}) : left;
        Tensor b = transpose_b ? transpose_tensor(right, {1, 0}) : right;
        if (a.dtype == 1 && b.dtype == 1) {
            int status = fsv_cuda_matmul_f32_host(
                reinterpret_cast<const float*>(a.data.data()),
                reinterpret_cast<const float*>(b.data.data()),
                reinterpret_cast<float*>(output.data.data()),
                static_cast<int>(m), static_cast<int>(k), static_cast<int>(n));
            if (status == 0) {
                if (alpha != 1.0f || bias) {
                    Tensor shift = bias
                        ? (bias->shape == output.shape ? *bias : broadcast_to(*bias, output.shape))
                        : Tensor{};
                    for (int64_t index = 0; index < m * n; ++index) {
                        double total = read_numeric(output, static_cast<std::size_t>(index)) * alpha;
                        if (bias) {
                            total += beta * read_numeric(shift, static_cast<std::size_t>(index));
                        }
                        write_numeric(output, static_cast<std::size_t>(index), total);
                    }
                }
                return output;
            }
        }
    }
    for (int64_t row = 0; row < m; ++row) {
        for (int64_t col = 0; col < n; ++col) {
            double total = 0.0;
            for (int64_t index = 0; index < k; ++index) {
                int64_t a_index = transpose_a ? index * m + row : row * k + index;
                int64_t b_index = transpose_b ? col * k + index : index * n + col;
                total += read_numeric(left, static_cast<std::size_t>(a_index)) *
                    read_numeric(right, static_cast<std::size_t>(b_index));
            }
            total *= alpha;
            if (bias && bias->dtype) {
                Tensor c = bias->shape == output.shape ? *bias : broadcast_to(*bias, output.shape);
                total += beta * read_numeric(c, static_cast<std::size_t>(row * n + col));
            }
            write_numeric(output, static_cast<std::size_t>(row * n + col), total);
        }
    }
    return output;
}

Tensor matmul_nbits_tensor(const Tensor& activation, const Tensor& packed, const Tensor& scales,
                           int64_t k, int64_t n, int64_t block_size) {
    ensure_host(activation);
    ensure_host(packed);
    ensure_host(scales);

    int64_t blocks = (k + block_size - 1) / block_size;
    int64_t m = activation.numel() / k;
    if (activation.shape.empty() || activation.shape.back() != k) return {};
    std::vector<int64_t> output_shape(
        activation.shape.begin(), activation.shape.end() - 1);
    output_shape.push_back(n);
    Tensor output = make_tensor(output_shape, activation.dtype);
    if (cuda_host_enabled() && activation.dtype == 1 && packed.dtype == 2 &&
        (scales.dtype == 1 || scales.dtype == 10) && m > 0 && blocks > 0) {
        int status = scales.dtype == 10
            ? fsv_cuda_matmul_nbits_f16scales_host(
                reinterpret_cast<const float*>(activation.data.data()),
                packed.data.data(),
                reinterpret_cast<const unsigned short*>(scales.data.data()),
                reinterpret_cast<float*>(output.data.data()),
                static_cast<int>(m), static_cast<int>(k), static_cast<int>(n),
                static_cast<int>(block_size), 4)
            : fsv_cuda_matmul_nbits_f32_host(
                reinterpret_cast<const float*>(activation.data.data()),
                packed.data.data(),
                reinterpret_cast<const float*>(scales.data.data()),
                reinterpret_cast<float*>(output.data.data()),
                static_cast<int>(m), static_cast<int>(k), static_cast<int>(n),
                static_cast<int>(block_size), 4);
        if (status == 0) return output;
    }
    for (int64_t row = 0; row < m; ++row) {
        for (int64_t col = 0; col < n; ++col) {
            double total = 0.0;
            for (int64_t index = 0; index < k; ++index) {
                int64_t local = index % block_size;
                int64_t block = index / block_size;
                std::uint8_t byte = read_scalar<std::uint8_t>(packed,
                    static_cast<std::size_t>((col * blocks + block) * (block_size / 2) + local / 2));
                int q = (byte >> (4 * (local & 1))) & 0x0f;
                double scale = read_numeric(scales,
                    static_cast<std::size_t>(col * blocks + block));
                total += read_numeric(activation,
                    static_cast<std::size_t>(row * k + index)) *
                    static_cast<double>(q - 8) * scale;
            }
            write_numeric(output, static_cast<std::size_t>(row * n + col), total);
        }
    }
    return output;
}

Tensor conv1d_tensor(const Tensor& input, const Tensor& weight, const Tensor* bias,
                     const std::vector<int64_t>& pads, const std::vector<int64_t>& strides,
                     const std::vector<int64_t>& dilations, int64_t groups) {
    ensure_host(input);
    ensure_host(weight);
    if (bias) ensure_host(*bias);

    if (input.shape.size() != 3 || weight.shape.size() != 3) return {};
    int64_t batch = input.shape[0];
    int64_t channels = input.shape[1];
    int64_t width = input.shape[2];
    int64_t out_channels = weight.shape[0];
    int64_t kernel = weight.shape[2];
    int64_t pad_left = pads.empty() ? 0 : pads[0];
    int64_t pad_right = pads.size() < 2 ? pad_left : pads[1];
    int64_t stride = strides.empty() ? 1 : strides[0];
    int64_t dilation = dilations.empty() ? 1 : dilations[0];
    int64_t out_width = (width + pad_left + pad_right -
                         (kernel - 1) * dilation - 1) / stride + 1;
    Tensor output = make_tensor({batch, out_channels, out_width}, input.dtype);
    if (cuda_host_enabled() && std::getenv("FSV_NATIVE_CPU_CONV1D") == nullptr &&
        input.dtype == 1 && weight.dtype == 1 &&
        (!bias || bias->dtype == 1)) {
        const float* bias_data = bias
            ? reinterpret_cast<const float*>(bias->data.data()) : nullptr;
        const int status = fsv_cuda_conv2d_f32_host(
            reinterpret_cast<const float*>(input.data.data()),
            reinterpret_cast<const float*>(weight.data.data()),
            bias_data,
            reinterpret_cast<float*>(output.data.data()),
            static_cast<int>(batch), static_cast<int>(channels), 1,
            static_cast<int>(width), static_cast<int>(out_channels), 1,
            static_cast<int>(kernel), 0, static_cast<int>(pad_left), 0,
            static_cast<int>(pad_right), 1, static_cast<int>(stride), 1,
            static_cast<int>(dilation), static_cast<int>(groups));
        if (status == 0) return output;
    }
    int64_t cin_group = channels / groups;
    int64_t cout_group = out_channels / groups;
    for (int64_t b = 0; b < batch; ++b) {
        for (int64_t oc = 0; oc < out_channels; ++oc) {
            int64_t group = oc / cout_group;
            for (int64_t ox = 0; ox < out_width; ++ox) {
                double total = bias ? read_numeric(*bias, static_cast<std::size_t>(oc)) : 0.0;
                for (int64_t ic = group * cin_group; ic < (group + 1) * cin_group; ++ic) {
                    int64_t local_ic = ic - group * cin_group;
                    for (int64_t kx = 0; kx < kernel; ++kx) {
                        int64_t ix = ox * stride - pad_left + kx * dilation;
                        if (ix < 0 || ix >= width) continue;
                        double x = read_numeric(input,
                            static_cast<std::size_t>((b * channels + ic) * width + ix));
                        double w = read_numeric(weight,
                            static_cast<std::size_t>((oc * cin_group + local_ic) * kernel + kx));
                        total += x * w;
                    }
                }
                std::size_t index = static_cast<std::size_t>((b * out_channels + oc) * out_width + ox);
                write_numeric(output, index, total);
            }
        }
    }
    if (std::getenv("FSV_CONV_DEBUG")) {
        FSV_TRACE( "[native-conv] out=%s bias=%s first=",
                     shape_text(output.shape).c_str(), bias ? "yes" : "no");
        for (int64_t index = 0; index < std::min<int64_t>(10, output.numel()); ++index) {
            FSV_TRACE( " %.9g", read_numeric(output, static_cast<std::size_t>(index)));
        }
        FSV_TRACE( "\n");
    }
    return output;
}

Tensor conv2d_tensor(const Tensor& input, const Tensor& weight, const Tensor* bias,
                     const std::vector<int64_t>& pads, const std::vector<int64_t>& strides,
                     const std::vector<int64_t>& dilations, int64_t groups) {
    ensure_host(input);
    ensure_host(weight);
    if (bias) ensure_host(*bias);

    if (input.shape.size() != 4 || weight.shape.size() != 4) return {};
    int64_t batch = input.shape[0];
    int64_t channels = input.shape[1];
    int64_t height = input.shape[2];
    int64_t width = input.shape[3];
    int64_t out_channels = weight.shape[0];
    int64_t kernel_h = weight.shape[2];
    int64_t kernel_w = weight.shape[3];
    int64_t pad_top = pads.empty() ? 0 : pads[0];
    int64_t pad_left = pads.size() < 2 ? pad_top : pads[1];
    int64_t pad_bottom = pads.size() < 3 ? pad_top : pads[2];
    int64_t pad_right = pads.size() < 4 ? pad_left : pads[3];
    int64_t stride_h = strides.empty() ? 1 : strides[0];
    int64_t stride_w = strides.size() < 2 ? stride_h : strides[1];
    int64_t dilation_h = dilations.empty() ? 1 : dilations[0];
    int64_t dilation_w = dilations.size() < 2 ? dilation_h : dilations[1];
    int64_t out_height = (height + pad_top + pad_bottom -
                          (kernel_h - 1) * dilation_h - 1) / stride_h + 1;
    int64_t out_width = (width + pad_left + pad_right -
                         (kernel_w - 1) * dilation_w - 1) / stride_w + 1;
    if (out_height <= 0 || out_width <= 0) return {};
    Tensor output = make_tensor({batch, out_channels, out_height, out_width}, input.dtype);
    if (cuda_host_enabled() && std::getenv("FSV_NATIVE_CPU_CONV2D") == nullptr &&
        input.dtype == 1 && weight.dtype == 1 &&
        (!bias || bias->dtype == 1)) {
        const float* bias_data = bias
            ? reinterpret_cast<const float*>(bias->data.data()) : nullptr;
        const int status = fsv_cuda_conv2d_f32_host(
            reinterpret_cast<const float*>(input.data.data()),
            reinterpret_cast<const float*>(weight.data.data()),
            bias_data,
            reinterpret_cast<float*>(output.data.data()),
            static_cast<int>(batch), static_cast<int>(channels),
            static_cast<int>(height), static_cast<int>(width),
            static_cast<int>(out_channels), static_cast<int>(kernel_h),
            static_cast<int>(kernel_w), static_cast<int>(pad_top),
            static_cast<int>(pad_left), static_cast<int>(pad_bottom),
            static_cast<int>(pad_right), static_cast<int>(stride_h),
            static_cast<int>(stride_w), static_cast<int>(dilation_h),
            static_cast<int>(dilation_w), static_cast<int>(groups));
        if (status == 0) return output;
    }
    int64_t cin_group = channels / groups;
    int64_t cout_group = out_channels / groups;
    for (int64_t b = 0; b < batch; ++b) {
        for (int64_t oc = 0; oc < out_channels; ++oc) {
            int64_t group = oc / cout_group;
            for (int64_t oy = 0; oy < out_height; ++oy) {
                for (int64_t ox = 0; ox < out_width; ++ox) {
                    double total = bias ? read_numeric(*bias, static_cast<std::size_t>(oc)) : 0.0;
                    for (int64_t ic = group * cin_group; ic < (group + 1) * cin_group; ++ic) {
                        int64_t local_ic = ic - group * cin_group;
                        for (int64_t ky = 0; ky < kernel_h; ++ky) {
                            int64_t iy = oy * stride_h - pad_top + ky * dilation_h;
                            if (iy < 0 || iy >= height) continue;
                            for (int64_t kx = 0; kx < kernel_w; ++kx) {
                                int64_t ix = ox * stride_w - pad_left + kx * dilation_w;
                                if (ix < 0 || ix >= width) continue;
                                double x = read_numeric(input, static_cast<std::size_t>(
                                    ((b * channels + ic) * height + iy) * width + ix));
                                double w = read_numeric(weight, static_cast<std::size_t>(
                                    (oc * cin_group + local_ic) * kernel_h * kernel_w +
                                    ky * kernel_w + kx));
                                total += x * w;
                            }
                        }
                    }
                    std::size_t index = static_cast<std::size_t>(
                        ((b * out_channels + oc) * out_height + oy) * out_width + ox);
                    write_numeric(output, index, total);
                }
            }
        }
    }
    return output;
}

Tensor conv_transpose1d_tensor(const Tensor& input, const Tensor& weight, const Tensor* bias,
                               const std::vector<int64_t>& pads,
                               const std::vector<int64_t>& strides,
                               const std::vector<int64_t>& dilations,
                               const std::vector<int64_t>& output_padding, int64_t groups) {
    ensure_host(input);
    ensure_host(weight);
    if (bias) ensure_host(*bias);

    if (input.shape.size() != 3 || weight.shape.size() != 3) return {};
    int64_t batch = input.shape[0];
    int64_t channels = input.shape[1];
    int64_t width = input.shape[2];
    int64_t out_channels_group = weight.shape[1];
    int64_t out_channels = out_channels_group * groups;
    int64_t kernel = weight.shape[2];
    int64_t pad_left = pads.empty() ? 0 : pads[0];
    int64_t pad_right = pads.size() < 2 ? pad_left : pads[1];
    int64_t stride = strides.empty() ? 1 : strides[0];
    int64_t dilation = dilations.empty() ? 1 : dilations[0];
    int64_t output_pad = output_padding.empty() ? 0 : output_padding[0];
    int64_t out_width = (width - 1) * stride + dilation * (kernel - 1) +
        output_pad - pad_left - pad_right + 1;
    if (out_width <= 0) return {};
    Tensor output = make_tensor({batch, out_channels, out_width}, input.dtype);
    if (cuda_host_enabled() && input.dtype == 1 && weight.dtype == 1 &&
        (!bias || bias->dtype == 1)) {
        const float* bias_data = bias
            ? reinterpret_cast<const float*>(bias->data.data()) : nullptr;
        const int status = fsv_cuda_conv_transpose1d_f32_host(
            reinterpret_cast<const float*>(input.data.data()),
            reinterpret_cast<const float*>(weight.data.data()),
            bias_data,
            reinterpret_cast<float*>(output.data.data()),
            static_cast<int>(batch), static_cast<int>(channels),
            static_cast<int>(width), static_cast<int>(out_channels),
            static_cast<int>(kernel), static_cast<int>(pad_left),
            static_cast<int>(pad_right), static_cast<int>(stride),
            static_cast<int>(dilation), static_cast<int>(groups),
            static_cast<int>(output_pad));
        if (status == 0) return output;
    }
    for (int64_t index = 0; index < output.numel(); ++index) {
        write_numeric(output, static_cast<std::size_t>(index),
            bias ? read_numeric(*bias, static_cast<std::size_t>(
                (index / out_width) % out_channels)) : 0.0);
    }
    int64_t cin_group = channels / groups;
    std::size_t bytes = element_size(input.dtype);
    for (int64_t b = 0; b < batch; ++b) {
        for (int64_t ic = 0; ic < channels; ++ic) {
            int64_t group = ic / cin_group;
            for (int64_t oc_local = 0; oc_local < out_channels_group; ++oc_local) {
                int64_t oc = group * out_channels_group + oc_local;
                for (int64_t ix = 0; ix < width; ++ix) {
                    for (int64_t kx = 0; kx < kernel; ++kx) {
                        int64_t ox = ix * stride - pad_left + kx * dilation;
                        if (ox < 0 || ox >= out_width) continue;
                        std::size_t source = static_cast<std::size_t>(
                            (b * channels + ic) * width + ix);
                        std::size_t weight_index = static_cast<std::size_t>(
                            (ic * out_channels_group + oc_local) * kernel + kx);
                        std::size_t destination = static_cast<std::size_t>(
                            (b * out_channels + oc) * out_width + ox);
                        double value = read_numeric(output, destination) +
                            read_numeric(input, source) * read_numeric(weight, weight_index);
                        write_numeric(output, destination, value);
                    }
                }
            }
        }
    }
    (void)bytes;
    return output;
}

bool is_power_of_two(std::int64_t value) {
    return value > 0 && (value & (value - 1)) == 0;
}

void fft_in_place(std::vector<std::complex<double>>& values, bool inverse) {
    const std::size_t size = values.size();
    if (size <= 1) return;
    for (std::size_t index = 1, reversed = 0; index < size; ++index) {
        std::size_t bit = size >> 1;
        while (reversed & bit) {
            reversed ^= bit;
            bit >>= 1;
        }
        reversed ^= bit;
        if (index < reversed) {
            std::swap(values[index], values[reversed]);
        }
    }
    for (std::size_t length = 2; length <= size; length <<= 1) {
        std::size_t half = length >> 1;
        const double angle = (inverse ? 2.0 : -2.0) * 3.14159265358979323846 /
            static_cast<double>(length);
        const std::complex<double> twiddle_step(std::cos(angle), std::sin(angle));
        for (std::size_t offset = 0; offset < size; offset += length) {
            std::complex<double> twiddle(1.0, 0.0);
            for (std::size_t index = 0; index < half; ++index) {
                const std::complex<double> even = values[offset + index];
                const std::complex<double> odd = values[offset + index + half] * twiddle;
                values[offset + index] = even + odd;
                values[offset + index + half] = even - odd;
                twiddle *= twiddle_step;
            }
        }
    }
    if (inverse) {
        const double scale = 1.0 / static_cast<double>(size);
        for (std::complex<double>& value : values) value *= scale;
    }
}

std::vector<std::complex<double>> dft_line(
    const std::vector<std::complex<double>>& input, std::int64_t length,
    bool inverse) {
    std::vector<std::complex<double>> values(static_cast<std::size_t>(length));
    const std::size_t count = std::min(input.size(), values.size());
    for (std::size_t index = 0; index < count; ++index) {
        values[index] = input[index];
    }
    if (is_power_of_two(length)) {
        fft_in_place(values, inverse);
        return values;
    }
    std::vector<std::complex<double>> output(static_cast<std::size_t>(length));
    const double direction = inverse ? 2.0 : -2.0;
    for (std::int64_t result = 0; result < length; ++result) {
        std::complex<double> sum(0.0, 0.0);
        for (std::int64_t sample = 0; sample < length; ++sample) {
            const double angle = direction * 3.14159265358979323846 *
                static_cast<double>(sample) * static_cast<double>(result) /
                static_cast<double>(length);
            sum += values[static_cast<std::size_t>(sample)] *
                std::complex<double>(std::cos(angle), std::sin(angle));
        }
        output[static_cast<std::size_t>(result)] = sum;
    }
    if (inverse) {
        const double scale = 1.0 / static_cast<double>(length);
        for (std::complex<double>& value : output) value *= scale;
    }
    return output;
}

std::complex<double> read_complex_sample(const Tensor& tensor,
                                         std::size_t offset,
                                         std::int64_t components) {
    if (components == 1) {
        return std::complex<double>(read_numeric(tensor, offset), 0.0);
    }
    return std::complex<double>(read_numeric(tensor, offset),
                                read_numeric(tensor, offset + 1));
}

Tensor dft_tensor(const Tensor& input, const Tensor* dft_length,
                  std::int64_t axis, bool inverse, bool onesided) {
    if (input.dtype != 1 && input.dtype != 11) return {};
    const std::int64_t rank = static_cast<std::int64_t>(input.shape.size());
    if (rank < 2) return {};
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank - 1) return {};
    const std::int64_t components = input.shape.back();
    if (components != 1 && components != 2) return {};
    const std::int64_t input_length = input.shape[static_cast<std::size_t>(axis)];
    std::int64_t length = input_length;
    if (inverse && onesided) length = 2 * (input_length - 1);
    if (dft_length && dft_length->dtype) length = read_int_scalar(*dft_length);
    if (length <= 0 || input_length <= 0) return {};
    const std::int64_t output_length =
        onesided && !inverse ? (length >> 1) + 1 : length;
    std::vector<std::int64_t> output_shape = input.shape;
    output_shape[static_cast<std::size_t>(axis)] = output_length;
    output_shape.back() = inverse && onesided ? 1 : 2;
    Tensor output = make_tensor(output_shape, input.dtype);

    const std::int64_t outer = numel(std::vector<std::int64_t>(
        input.shape.begin(), input.shape.begin() + static_cast<std::size_t>(axis)));
    const std::int64_t inner = numel(std::vector<std::int64_t>(
        input.shape.begin() + static_cast<std::size_t>(axis) + 1,
        input.shape.end())) / components;
    const std::int64_t input_trailing =
        numel(std::vector<std::int64_t>(
            input.shape.begin() + static_cast<std::size_t>(axis) + 1,
            input.shape.end()));
    const std::int64_t output_components = inverse && onesided ? 1 : 2;
    const std::int64_t output_trailing =
        numel(std::vector<std::int64_t>(
            output_shape.begin() + static_cast<std::size_t>(axis) + 1,
            output_shape.end()));

    for (std::int64_t o = 0; o < outer; ++o) {
        for (std::int64_t i = 0; i < inner; ++i) {
            std::vector<std::complex<double>> signal(
                static_cast<std::size_t>(input_length));
            std::size_t base = static_cast<std::size_t>(
                (o * input_length) * input_trailing + i * components);
            for (std::int64_t sample = 0; sample < input_length; ++sample) {
                signal[static_cast<std::size_t>(sample)] = read_complex_sample(
                    input, base + static_cast<std::size_t>(sample) * input_trailing,
                    components);
            }
            if (inverse && onesided) {
                std::vector<std::complex<double>> spectrum(
                    static_cast<std::size_t>(length));
                const std::size_t available = std::min<std::size_t>(
                    signal.size(), spectrum.size());
                if (available > 0) spectrum[0] = signal[0];
                for (std::size_t index = 1; index < available; ++index) {
                    spectrum[index] = signal[index];
                    if (index * 2 < static_cast<std::size_t>(length)) {
                        spectrum[static_cast<std::size_t>(length) - index] =
                            std::conj(signal[index]);
                    }
                }
                signal = std::move(spectrum);
            }
            const std::vector<std::complex<double>> transformed =
                dft_line(signal, length, inverse);
            std::size_t output_base = static_cast<std::size_t>(
                (o * output_length) * output_trailing + i * output_components);
            for (std::int64_t result = 0; result < output_length; ++result) {
                const std::size_t offset = output_base +
                    static_cast<std::size_t>(result) * output_trailing;
                const std::complex<double>& value =
                    transformed[static_cast<std::size_t>(result)];
                write_numeric(output, offset, value.real());
                if (output_components == 2) {
                    write_numeric(output, offset + 1, value.imag());
                }
            }
        }
    }
    return output;
}

Tensor stft_tensor(const Tensor& signal, const Tensor& frame_step,
                   const Tensor* window, const Tensor* frame_length,
                   bool onesided) {
    if (signal.dtype != 1 && signal.dtype != 11) return {};
    if (signal.shape.size() != 2 && signal.shape.size() != 3) return {};
    const std::int64_t batch = signal.shape[0];
    const std::int64_t signal_length = signal.shape[1];
    const std::int64_t components = signal.shape.size() == 3 ? signal.shape[2] : 1;
    if (components != 1 && components != 2) return {};
    const std::int64_t step = read_int_scalar(frame_step);
    const std::int64_t requested_length = frame_length &&
        frame_length->dtype ? read_int_scalar(*frame_length) : step;
    const std::int64_t window_size = window && window->dtype ?
        window->shape[0] : requested_length;
    if (frame_length && frame_length->dtype && window && window->dtype &&
        requested_length != window_size) return {};
    if (step <= 0 || window_size <= 0 || signal_length < window_size) return {};
    const std::int64_t frames = (signal_length - window_size) / step + 1;
    const std::int64_t output_size = onesided ? (window_size >> 1) + 1 : window_size;
    Tensor output = make_tensor({batch, frames, output_size, 2}, signal.dtype);

    std::vector<std::complex<double>> window_values(
        static_cast<std::size_t>(window_size), std::complex<double>(1.0, 0.0));
    if (window && window->dtype) {
        for (std::int64_t index = 0; index < window_size; ++index) {
            window_values[static_cast<std::size_t>(index)] = std::complex<double>(
                read_numeric(*window, static_cast<std::size_t>(index)), 0.0);
        }
    }
    for (std::int64_t b = 0; b < batch; ++b) {
        for (std::int64_t frame = 0; frame < frames; ++frame) {
            const std::int64_t start = frame * step;
            std::vector<std::complex<double>> values(
                static_cast<std::size_t>(window_size));
            for (std::int64_t sample = 0; sample < window_size; ++sample) {
                const std::size_t offset = static_cast<std::size_t>(
                    (b * signal_length + start + sample) * components);
                values[static_cast<std::size_t>(sample)] =
                    read_complex_sample(signal, offset, components) *
                    window_values[static_cast<std::size_t>(sample)];
            }
            const std::vector<std::complex<double>> transformed =
                dft_line(values, window_size, false);
            for (std::int64_t result = 0; result < output_size; ++result) {
                const std::size_t offset = static_cast<std::size_t>(
                    ((b * frames + frame) * output_size + result) * 2);
                write_numeric(output, offset,
                              transformed[static_cast<std::size_t>(result)].real());
                write_numeric(output, offset + 1,
                              transformed[static_cast<std::size_t>(result)].imag());
            }
        }
    }
    return output;
}

/* -------------------------------------------------------------------------
   Device execution.

   Every helper below runs one ONNX node on the GPU when its operands are
   float32 tensors. It returns true with the result left device-resident; when
   it declines it first makes every operand host-current, because the CPU
   implementation taking over reads host bytes. Only the index tables a node
   needs travel from the host, so a chain of these operators never round-trips
   through system memory.
   ------------------------------------------------------------------------- */

/* A card that has run out of memory declines every remaining node, so only the
   first refusal is reported: one line per node would bury the log. */
void warn_device_shortage() {
    static bool warned = false;
    if (warned) return;
    warned = true;
    std::fprintf(stderr,
                 "[fsv-cuda] 显存不足：算子改由主机端执行，本次推理会显著变慢。\n");
}

bool device_decline(const std::vector<Tensor*>& inputs) {
    fsv::opstats::count_decline();
    if (fsv_cuda_take_alloc_failure()) warn_device_shortage();
    /* Temporary diagnostic (FSV_NATIVE_DECL_LOG): a node that leaves the
       device path names the operand that pushed it off, so the missing dtype
       or rank cases can be counted instead of guessed. */
    static const bool log_declines = std::getenv("FSV_NATIVE_DECL_LOG") != nullptr;
    if (log_declines) {
        std::string text;
        char part[128];
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            const Tensor* input = inputs[index];
            if (!input) {
                text += " null";
                continue;
            }
            std::snprintf(part, sizeof(part), " %s/d%d",
                          shape_text(input->shape).c_str(), input->dtype);
            text += part;
            if (input->device) {
                text += input->device->host_valid ? ":d+h" : ":d";
            } else {
                text += ":h";
            }
        }
        std::fprintf(stderr, "[decl] %s%s\n", fsv::opstats::current(), text.c_str());
    }
    for (Tensor* input : inputs) {
        if (input) ensure_host(*input);
    }
    return false;
}

/* True when the first `count` operands exist and hold float32 data. */
bool device_ready(const std::vector<Tensor*>& inputs, std::size_t count) {
    if (!cuda_host_enabled() || inputs.size() < count) return false;
    for (std::size_t index = 0; index < count; ++index) {
        if (!inputs[index] || inputs[index]->dtype != 1) return false;
        if (inputs[index]->shape.size() > 8) return false;
    }
    return true;
}

/* Right-aligned broadcast strides in output dimension order; a dimension the
   operand broadcasts contributes a zero stride. */
void fill_broadcast_strides(const std::vector<int64_t>& input,
                            const std::vector<int64_t>& output, long long* strides) {
    const std::vector<int64_t> own = strides_for(input);
    const int rank = static_cast<int>(output.size());
    for (int dim = 0; dim < rank; ++dim) {
        const int source = dim - (rank - static_cast<int>(input.size()));
        if (source < 0 || input[static_cast<std::size_t>(source)] == 1) strides[dim] = 0;
        else strides[dim] = own[static_cast<std::size_t>(source)];
    }
}

bool device_binary(const std::vector<Tensor*>& inputs, int operation, Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    const std::vector<int64_t> shape = broadcast_shape(*inputs[0], *inputs[1]);
    if (shape.size() > 8 ||
        (shape.empty() && !(inputs[0]->shape.empty() && inputs[1]->shape.empty()))) {
        return device_decline(inputs);
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr left = ensure_device(*inputs[0]);
    const fsv_cuda_ptr right = ensure_device(*inputs[1]);
    fsv_cuda_index index = {};
    for (std::size_t dim = 0; dim < shape.size(); ++dim) index.shape[dim] = shape[dim];
    fill_broadcast_strides(inputs[0]->shape, shape, index.a_stride);
    fill_broadcast_strides(inputs[1]->shape, shape, index.b_stride);
    index.rank = static_cast<int>(shape.size());
    index.operation = operation;
    if (!left || !right ||
        fsv_cuda_binary_bcast_f32(left, right, output.device->pointer,
                                  static_cast<std::size_t>(count), &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    layout_shape_note("binary " + shape_text(inputs[0]->shape) + " " +
                      shape_text(inputs[1]->shape) + " -> " + shape_text(shape));
    return true;
}

bool device_max(const std::vector<Tensor*>& inputs, Tensor& output) {
    if (inputs.size() < 2) return device_decline(inputs);
    if (!device_binary(std::vector<Tensor*>{inputs[0], inputs[1]}, 5, output)) return false;
    for (std::size_t index = 2; index < inputs.size(); ++index) {
        Tensor combined;
        if (!device_binary(std::vector<Tensor*>{&output, inputs[index]}, 5, combined)) return false;
        output = std::move(combined);
    }
    return true;
}

/* Where(condition, a, b) with a byte condition and float operands. The host
   implementation downloads all three operands, which drained the pipeline once
   per generated frame: the decoder builds its attention mask this way. */
bool device_where(const std::vector<Tensor*>& inputs, Tensor& output) {
    if (!cuda_host_enabled() || inputs.size() < 3) return device_decline(inputs);
    const Tensor& condition = *inputs[0];
    const Tensor& left = *inputs[1];
    const Tensor& right = *inputs[2];
    if (condition.dtype != 9 || left.dtype != 1 || right.dtype != 1) {
        return device_decline(inputs);
    }
    if (condition.shape.size() > 8 || left.shape.size() > 8 || right.shape.size() > 8) {
        return device_decline(inputs);
    }
    const std::vector<int64_t> shape = broadcast_shape_vectors(
        broadcast_shape_vectors(condition.shape, left.shape), right.shape);
    if (shape.size() > 8 ||
        (shape.empty() && !(condition.shape.empty() && left.shape.empty() &&
                            right.shape.empty()))) {
        return device_decline(inputs);
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr condition_device = ensure_device(*inputs[0]);
    const fsv_cuda_ptr left_device = ensure_device(*inputs[1]);
    const fsv_cuda_ptr right_device = ensure_device(*inputs[2]);
    fsv_cuda_where_index index = {};
    for (std::size_t dim = 0; dim < shape.size(); ++dim) index.shape[dim] = shape[dim];
    fill_broadcast_strides(condition.shape, shape, index.condition_stride);
    fill_broadcast_strides(left.shape, shape, index.a_stride);
    fill_broadcast_strides(right.shape, shape, index.b_stride);
    index.rank = static_cast<int>(shape.size());
    if (!condition_device || !left_device || !right_device ||
        fsv_cuda_where_f32(condition_device, left_device, right_device,
                           output.device->pointer, static_cast<std::size_t>(count),
                           &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_compare(const std::vector<Tensor*>& inputs, int operation, Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    const std::vector<int64_t> shape = broadcast_shape(*inputs[0], *inputs[1]);
    if (shape.size() > 8 ||
        (shape.empty() && !(inputs[0]->shape.empty() && inputs[1]->shape.empty()))) {
        return device_decline(inputs);
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(shape, 9);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr left = ensure_device(*inputs[0]);
    const fsv_cuda_ptr right = ensure_device(*inputs[1]);
    fsv_cuda_index index = {};
    for (std::size_t dim = 0; dim < shape.size(); ++dim) index.shape[dim] = shape[dim];
    fill_broadcast_strides(inputs[0]->shape, shape, index.a_stride);
    fill_broadcast_strides(inputs[1]->shape, shape, index.b_stride);
    index.rank = static_cast<int>(shape.size());
    index.operation = operation;
    if (!left || !right ||
        fsv_cuda_compare_bcast_u8(left, right, output.device->pointer,
                                  static_cast<std::size_t>(count), &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_unary(const std::vector<Tensor*>& inputs, int operation, float alpha,
                  Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int64_t count = source.numel();
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(source.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    if (!value || fsv_cuda_unary_ops_f32(value, output.device->pointer,
                                         static_cast<std::size_t>(count), operation,
                                         alpha) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_clip(const std::vector<Tensor*>& inputs, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    /* Clip bounds are scalars in every graph this runtime serves; a tensor
       bound would need a broadcast clamp and stays on the CPU. */
    for (std::size_t index = 1; index < inputs.size(); ++index) {
        if (inputs[index] && inputs[index]->numel() != 1) return device_decline(inputs);
    }
    const Tensor& source = *inputs[0];
    const int64_t count = source.numel();
    if (count <= 0) return device_decline(inputs);
    const bool has_low = inputs.size() > 1 && inputs[1];
    const bool has_high = inputs.size() > 2 && inputs[2];
    const float low = has_low ? static_cast<float>(read_numeric(*inputs[1], 0)) : 0.0f;
    const float high = has_high ? static_cast<float>(read_numeric(*inputs[2], 0)) : 0.0f;
    output = device_tensor(source.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    if (!value || fsv_cuda_clamp_f32(value, output.device->pointer,
                                     static_cast<std::size_t>(count), has_low ? 1 : 0, low,
                                     has_high ? 1 : 0, high) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}
bool device_transpose(const std::vector<Tensor*>& inputs, const std::vector<int64_t>& perm,
                      Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int rank = static_cast<int>(source.shape.size());
    if (rank <= 0 || perm.size() != source.shape.size()) return device_decline(inputs);
    std::vector<int64_t> shape(perm.size());
    for (std::size_t index = 0; index < perm.size(); ++index) {
        if (perm[index] < 0 || perm[index] >= rank) return device_decline(inputs);
        shape[index] = source.shape[static_cast<std::size_t>(perm[index])];
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    {
        std::string note = "transpose " + shape_text(source.shape) + " perm";
        for (int64_t value : perm) note += " " + std::to_string(value);
        note += " -> " + shape_text(shape);
        layout_shape_note(note);
    }
    const std::vector<int64_t> own = strides_for(source.shape);
    const std::vector<int64_t> out_strides = strides_for(shape);
    /* A permutation that leaves the memory layout alone moves no bytes, it only
       relabels the axes. The exported graphs are full of those -- a rank-4
       shape whose reordered axes both have extent one, for instance -- and
       issuing a kernel for each was thousands of launches a synthesis. */
    bool layout_identity = true;
    for (int dim = 0; dim < rank; ++dim) {
        if (shape[static_cast<std::size_t>(dim)] == 1) continue;
        if (own[static_cast<std::size_t>(perm[static_cast<std::size_t>(dim)])] !=
            out_strides[static_cast<std::size_t>(dim)]) {
            layout_identity = false;
            break;
        }
    }
    if (layout_identity) {
        output = *inputs[0];
        output.shape = shape;
        return true;
    }
    /* A permutation that exchanges exactly two axes is a rectangular block move
       and has a much better kernel than the generic coordinate walk. */
    int first = -1;
    int second = -1;
    for (int dim = 0; dim < rank; ++dim) {
        if (perm[static_cast<std::size_t>(dim)] == dim) continue;
        if (first < 0) first = dim;
        else if (second < 0) second = dim;
        else { first = -1; break; }
    }
    const bool is_swap = first >= 0 && second >= 0 &&
        perm[static_cast<std::size_t>(first)] == second &&
        perm[static_cast<std::size_t>(second)] == first;
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    if (!value) return device_decline(inputs);
    if (is_swap) {
        const int low = first < second ? first : second;
        const int high = first < second ? second : first;
        int64_t outer = 1;
        for (int dim = 0; dim < low; ++dim) outer *= source.shape[static_cast<std::size_t>(dim)];
        int64_t middle = 1;
        for (int dim = low + 1; dim < high; ++dim) {
            middle *= source.shape[static_cast<std::size_t>(dim)];
        }
        int64_t inner = 1;
        for (int dim = high + 1; dim < rank; ++dim) {
            inner *= source.shape[static_cast<std::size_t>(dim)];
        }
        const int64_t a_size = source.shape[static_cast<std::size_t>(low)];
        const int64_t b_size = source.shape[static_cast<std::size_t>(high)];
        if (middle == 1 && (a_size > 1 || b_size > 1)) {
            output = device_tensor(shape, 1);
            if (!adopt_device_output(output)) return device_decline(inputs);
            const int status = inner == 1
                ? fsv_cuda_transpose_tile_f32(value, output.device->pointer, outer,
                                              a_size, b_size)
                : fsv_cuda_transpose_swap_f32(value, output.device->pointer, a_size,
                                              b_size, inner);
            if (status != 0) {
                output = Tensor();
                return device_decline(inputs);
            }
            return true;
        }
    }
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    fsv_cuda_index index = {};
    for (int dim = 0; dim < rank; ++dim) {
        index.shape[dim] = shape[static_cast<std::size_t>(dim)];
        index.a_stride[dim] = own[static_cast<std::size_t>(perm[static_cast<std::size_t>(dim)])];
    }
    if (!value || fsv_cuda_transpose_f32(value, output.device->pointer,
                                         static_cast<std::size_t>(count), rank,
                                         &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_concat(const std::vector<Tensor*>& inputs, int64_t axis, Tensor& output) {
    if (!cuda_host_enabled() || inputs.empty()) return device_decline(inputs);
    for (const Tensor* input : inputs) {
        if (!input || input->dtype != 1 || input->shape.size() > 8) return device_decline(inputs);
    }
    std::vector<int64_t> shape = inputs[0]->shape;
    const int rank = static_cast<int>(shape.size());
    if (axis < 0) axis += rank;
    if (rank <= 0 || axis < 0 || axis >= rank) return device_decline(inputs);
    shape[static_cast<std::size_t>(axis)] = 0;
    for (const Tensor* input : inputs) {
        if (input->shape.size() != inputs[0]->shape.size()) return device_decline(inputs);
        shape[static_cast<std::size_t>(axis)] += input->shape[static_cast<std::size_t>(axis)];
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const std::vector<int64_t> destination_strides = strides_for(shape);
    {
        std::string note = "concat axis=" + std::to_string(axis) + " out " +
                           shape_text(shape);
        for (Tensor* input : inputs) note += " " + shape_text(input->shape);
        layout_shape_note(note);
    }
    int64_t offset = 0;
    for (Tensor* input : inputs) {
        const int64_t item_count = input->numel();
        if (item_count > 0) {
            const std::vector<int64_t> own = strides_for(input->shape);
            fsv_cuda_index index = {};
            for (int dim = 0; dim < rank; ++dim) {
                index.shape[dim] = input->shape[static_cast<std::size_t>(dim)];
                index.a_stride[dim] = own[static_cast<std::size_t>(dim)];
                index.b_stride[dim] = destination_strides[static_cast<std::size_t>(dim)];
            }
            const fsv_cuda_ptr value = ensure_device(*input);
            if (!value ||
                fsv_cuda_copy_nd_f32(value, output.device->pointer,
                                     static_cast<std::size_t>(item_count), 0,
                                     offset * destination_strides[static_cast<std::size_t>(axis)],
                                     rank, &index) != 0) {
                output = Tensor();
                return device_decline(inputs);
            }
        }
        offset += input->shape[static_cast<std::size_t>(axis)];
    }
    return true;
}

bool device_slice(const std::vector<Tensor*>& inputs, const std::vector<int64_t>& starts,
                  const std::vector<int64_t>& ends, const std::vector<int64_t>& axes,
                  const std::vector<int64_t>& steps, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    /* Only a unit step cuts a contiguous box out of the tensor. */
    for (int64_t step : steps) {
        if (step != 1) return device_decline(inputs);
    }
    const Tensor& source = *inputs[0];
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0) return device_decline(inputs);
    std::vector<int64_t> axis_list = axes;
    if (axis_list.empty()) {
        for (int64_t axis = 0; axis < rank; ++axis) axis_list.push_back(axis);
    }
    if (starts.size() > axis_list.size() || ends.size() > axis_list.size()) {
        return device_decline(inputs);
    }
    std::vector<int64_t> shape = source.shape;
    std::vector<int64_t> begin(source.shape.size(), 0);
    for (std::size_t index = 0; index < axis_list.size(); ++index) {
        int64_t axis = axis_list[index];
        if (axis < 0) axis += rank;
        if (axis < 0 || axis >= rank) return device_decline(inputs);
        const int64_t dimension = source.shape[static_cast<std::size_t>(axis)];
        int64_t start = index < starts.size() ? starts[index] : 0;
        int64_t end = index < ends.size() ? ends[index] : dimension;
        if (start < 0) start += dimension;
        if (end < 0) end += dimension;
        start = std::max<int64_t>(0, std::min(start, dimension));
        end = std::max<int64_t>(0, std::min(end, dimension));
        if (end < start) end = start;
        begin[static_cast<std::size_t>(axis)] = start;
        shape[static_cast<std::size_t>(axis)] = end - start;
    }
    const int64_t count = numel(shape);
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const std::vector<int64_t> own = strides_for(source.shape);
    const std::vector<int64_t> destination_strides = strides_for(shape);
    int64_t base = 0;
    fsv_cuda_index index = {};
    for (int64_t dim = 0; dim < rank; ++dim) {
        index.shape[static_cast<std::size_t>(dim)] = shape[static_cast<std::size_t>(dim)];
        index.a_stride[static_cast<std::size_t>(dim)] = own[static_cast<std::size_t>(dim)];
        index.b_stride[static_cast<std::size_t>(dim)] = destination_strides[static_cast<std::size_t>(dim)];
        base += begin[static_cast<std::size_t>(dim)] * own[static_cast<std::size_t>(dim)];
    }
    if (!value || fsv_cuda_copy_nd_f32(value, output.device->pointer,
                                       static_cast<std::size_t>(count), base, 0,
                                       static_cast<int>(rank), &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    layout_shape_note("slice " + shape_text(source.shape) + " -> " + shape_text(shape));
    return true;
}

bool device_gather(const std::vector<Tensor*>& inputs, int64_t axis, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    /* The index operand may have any rank, including the scalar constants the
       exported decoders use to pick one embedding row: the kernel already
       treats the indices as a flat run of `index_count` positions and the
       output shape below concatenates whatever rank they have. Rejecting
       rank 0 and rank 2 here sent every such Gather down the host path, which
       downloaded the activation it indexed (61 steps x 3 nodes, the single
       largest host-side cost in a synthesis). */
    if (inputs.size() < 2 || !inputs[1] || inputs[1]->dtype != 7) {
        return device_decline(inputs);
    }
    const Tensor& data = *inputs[0];
    const int64_t rank = static_cast<int64_t>(data.shape.size());
    if (axis < 0) axis += rank;
    if (rank <= 0 || axis < 0 || axis >= rank) return device_decline(inputs);
    const int64_t axis_size = data.shape[static_cast<std::size_t>(axis)];
    const std::vector<int64_t> outer_dims(data.shape.begin(), data.shape.begin() + axis);
    const std::vector<int64_t> inner_dims(data.shape.begin() + axis + 1, data.shape.end());
    const int64_t outer = numel(outer_dims);
    const int64_t inner = numel(inner_dims);
    const int64_t index_count = inputs[1]->numel();
    if (outer <= 0 || inner <= 0 || index_count <= 0 || axis_size <= 0) {
        return device_decline(inputs);
    }
    std::vector<int64_t> shape = outer_dims;
    shape.insert(shape.end(), inputs[1]->shape.begin(), inputs[1]->shape.end());
    shape.insert(shape.end(), inner_dims.begin(), inner_dims.end());
    if (shape.size() > 8) return device_decline(inputs);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const fsv_cuda_ptr positions = ensure_device(*inputs[1]);
    if (!value || !positions ||
        fsv_cuda_gather_f32(value, positions, output.device->pointer, outer, index_count,
                            inner, axis_size) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* GatherElements keeps the index tensor's shape and takes every coordinate but
   the indexed one from the output slot. The node used to run on the host, so
   each step downloaded the activation it indexed just to read one row back;
   the decode loop pays that stall 61 times a synthesis. */
bool device_gather_elements(const std::vector<Tensor*>& inputs, int64_t axis, Tensor& output) {
    /* Only the data operand is float32; the indices are int64 and the kernel
       reads them as such. */
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    if (inputs.size() < 2 || !inputs[1] || inputs[1]->dtype != 7) return device_decline(inputs);
    const Tensor& data = *inputs[0];
    const Tensor& positions = *inputs[1];
    const int64_t rank = static_cast<int64_t>(data.shape.size());
    if (rank <= 0 || rank > 8) return device_decline(inputs);
    if (positions.shape.size() != data.shape.size()) return device_decline(inputs);
    int64_t indexed = axis;
    if (indexed < 0) indexed += rank;
    if (indexed < 0 || indexed >= rank) return device_decline(inputs);
    for (int64_t dim = 0; dim < rank; ++dim) {
        if (dim == indexed) continue;
        if (data.shape[static_cast<std::size_t>(dim)] !=
            positions.shape[static_cast<std::size_t>(dim)]) {
            return device_decline(inputs);
        }
    }
    const int64_t count = positions.numel();
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(positions.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const fsv_cuda_ptr slots = ensure_device(*inputs[1]);
    const std::vector<int64_t> own = strides_for(data.shape);
    fsv_cuda_index index = {};
    for (int64_t dim = 0; dim < rank; ++dim) {
        index.shape[dim] = positions.shape[static_cast<std::size_t>(dim)];
        index.a_stride[dim] = own[static_cast<std::size_t>(dim)];
    }
    index.rank = static_cast<int>(rank);
    if (!value || !slots ||
        fsv_cuda_gather_elements_f32(value, slots, output.device->pointer,
                                     static_cast<std::size_t>(count), indexed,
                                     data.shape[static_cast<std::size_t>(indexed)],
                                     &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* ScatterElements is the dual of GatherElements above and ran on the host for
   the same reason: the fallback downloads both operands, and the T2S decoder
   evaluates it once per step, so the queue is drained 63 times a synthesis
   for a node whose arithmetic is trivial. The device path lays down the copy
   of the operand and scatters the updates with two launches instead. */
bool device_scatter_elements(const std::vector<Tensor*>& inputs, int64_t axis,
                             const std::string& reduction, Tensor& output) {
    /* The reduction variants are a different operator; only "none" is the
       overwrite this kernel and the host reference implement. */
    if (reduction != "none" && !reduction.empty()) return device_decline(inputs);
    if (inputs.size() < 3 || !inputs[1] || !inputs[2]) return device_decline(inputs);
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    /* Indices travel as int64 integers and the updates as float32, the same
       split GatherElements uses. */
    if (inputs[1]->dtype != 7 || inputs[2]->dtype != 1) return device_decline(inputs);
    const Tensor& data = *inputs[0];
    const Tensor& positions = *inputs[1];
    const Tensor& updates = *inputs[2];
    const int64_t rank = static_cast<int64_t>(data.shape.size());
    if (rank <= 0 || rank > 8) return device_decline(inputs);
    if (positions.shape.size() != data.shape.size() ||
        updates.shape.size() != data.shape.size()) {
        return device_decline(inputs);
    }
    int64_t indexed = axis;
    if (indexed < 0) indexed += rank;
    if (indexed < 0 || indexed >= rank) return device_decline(inputs);
    for (int64_t dim = 0; dim < rank; ++dim) {
        if (dim == indexed) continue;
        if (data.shape[static_cast<std::size_t>(dim)] !=
                positions.shape[static_cast<std::size_t>(dim)] ||
            data.shape[static_cast<std::size_t>(dim)] !=
                updates.shape[static_cast<std::size_t>(dim)]) {
            return device_decline(inputs);
        }
    }
    if (positions.shape[static_cast<std::size_t>(indexed)] !=
        updates.shape[static_cast<std::size_t>(indexed)]) {
        return device_decline(inputs);
    }
    const int64_t count = positions.numel();
    if (count <= 0 || data.numel() <= 0) return device_decline(inputs);
    output = device_tensor(data.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr source = ensure_device(*inputs[0]);
    const fsv_cuda_ptr slots = ensure_device(*inputs[1]);
    const fsv_cuda_ptr values = ensure_device(*inputs[2]);
    const std::vector<int64_t> own = strides_for(data.shape);
    fsv_cuda_index index = {};
    for (int64_t dim = 0; dim < rank; ++dim) {
        index.shape[dim] = positions.shape[static_cast<std::size_t>(dim)];
        index.a_stride[dim] = own[static_cast<std::size_t>(dim)];
        /* The result is contiguous and shares the operand's shape, so the same
           stride row describes both sides of the copy. */
        index.b_stride[dim] = own[static_cast<std::size_t>(dim)];
    }
    index.rank = static_cast<int>(rank);
    const fsv_cuda_ptr destination = output.device->pointer;
    if (!source || !slots || !values ||
        fsv_cuda_copy_nd_f32(source, destination, static_cast<std::size_t>(data.numel()),
                             0, 0, static_cast<int>(rank), &index) != 0 ||
        fsv_cuda_scatter_elements_f32(values, slots, destination,
                                      static_cast<std::size_t>(count), indexed,
                                      data.shape[static_cast<std::size_t>(indexed)],
                                      &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* Split along one axis, one device-to-device box copy per part. The host
   version downloaded the whole operand, cut it and uploaded every part again,
   which cost 106 MB of round trips per synthesis. */
bool device_split(const std::vector<Tensor*>& inputs, const std::vector<int64_t>& split,
                  int64_t axis, std::vector<Tensor>& outputs) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0 || rank > 8 || split.empty()) return device_decline(inputs);
    int64_t split_axis = axis;
    if (split_axis < 0) split_axis += rank;
    if (split_axis < 0 || split_axis >= rank) return device_decline(inputs);
    int64_t total = 0;
    for (int64_t value : split) total += value;
    if (total != source.shape[static_cast<std::size_t>(split_axis)]) {
        return device_decline(inputs);
    }
    std::vector<int64_t> probe = source.shape;
    for (int64_t value : split) {
        if (value <= 0) return device_decline(inputs);
        probe[static_cast<std::size_t>(split_axis)] = value;
        if (numel(probe) <= 0) return device_decline(inputs);
    }
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    if (!value) return device_decline(inputs);
    const std::vector<int64_t> own = strides_for(source.shape);
    std::vector<Tensor> parts;
    parts.reserve(split.size());
    int64_t start = 0;
    for (int64_t size : split) {
        std::vector<int64_t> shape = source.shape;
        shape[static_cast<std::size_t>(split_axis)] = size;
        Tensor part = device_tensor(shape, 1);
        if (!adopt_device_output(part)) return device_decline(inputs);
        const std::vector<int64_t> destination = strides_for(shape);
        fsv_cuda_index index = {};
        for (int64_t dim = 0; dim < rank; ++dim) {
            index.shape[dim] = shape[static_cast<std::size_t>(dim)];
            index.a_stride[dim] = own[static_cast<std::size_t>(dim)];
            index.b_stride[dim] = destination[static_cast<std::size_t>(dim)];
        }
        index.rank = static_cast<int>(rank);
        if (fsv_cuda_copy_nd_f32(value, part.device->pointer,
                                 static_cast<std::size_t>(numel(shape)),
                                 start * own[static_cast<std::size_t>(split_axis)], 0,
                                 static_cast<int>(rank), &index) != 0) {
            return device_decline(inputs);
        }
        parts.push_back(std::move(part));
        start += size;
    }
    outputs = std::move(parts);
    return true;
}

/* Byte offset into a resident buffer: the driver pointer is an unsigned
   integer, so the element offset has to be scaled before it is added. */
unsigned long long device_offset(fsv_cuda_ptr base, int64_t elements) {
    return base + static_cast<unsigned long long>(elements) * sizeof(float);
}

bool device_matmul(const std::vector<Tensor*>& inputs, Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    const Tensor& left_operand = *inputs[0];
    const Tensor& right_operand = *inputs[1];
    if (left_operand.shape.empty() || right_operand.shape.empty()) return device_decline(inputs);
    const bool left_vector = left_operand.shape.size() == 1;
    const bool right_vector = right_operand.shape.size() == 1;
    std::vector<int64_t> left_shape = left_operand.shape;
    std::vector<int64_t> right_shape = right_operand.shape;
    if (left_vector) left_shape.insert(left_shape.begin(), 1);
    if (right_vector) right_shape.push_back(1);
    const int64_t m = left_shape[left_shape.size() - 2];
    const int64_t k = left_shape.back();
    if (k != right_shape[right_shape.size() - 2]) return device_decline(inputs);
    const int64_t n = right_shape.back();
    if (m <= 0 || k <= 0 || n <= 0) return device_decline(inputs);
    const std::vector<int64_t> left_batch(left_shape.begin(), left_shape.end() - 2);
    const std::vector<int64_t> right_batch(right_shape.begin(), right_shape.end() - 2);
    const std::vector<int64_t> batch_shape = broadcast_shape_vectors(left_batch, right_batch);
    if (batch_shape.empty() && !(left_batch.empty() && right_batch.empty())) {
        return device_decline(inputs);
    }
    if (batch_shape.size() > 6) return device_decline(inputs);
    std::vector<int64_t> output_shape = batch_shape;
    output_shape.push_back(m);
    output_shape.push_back(n);
    if (left_vector) output_shape.erase(output_shape.end() - 2);
    if (right_vector) output_shape.erase(output_shape.end() - 1);
    output = device_tensor(output_shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr left = ensure_device(*inputs[0]);
    const fsv_cuda_ptr right = ensure_device(*inputs[1]);
    if (!left || !right) {
        output = Tensor();
        return device_decline(inputs);
    }
    /* One launch for the whole stack: the batch coordinates travel in the
       index table instead of one fsv_cuda_matmul_dev call per slice, which the
       profiler showed as 48913 launches inside 3177 MatMul nodes.

       The strides come from the left/right batch shapes right aligned against
       the broadcast batch shape, so a leading dimension one operand lacks and
       a dimension of size 1 both become a zero stride. The old per-slice loop
       added `coordinate * m * k` per dimension instead, which only produced
       the right offset while at most one batch dimension was non-broadcast. */
    const int64_t batch_size = numel(batch_shape);
    const int64_t batch_rank = static_cast<int64_t>(batch_shape.size());
    const std::vector<int64_t> left_batch_strides = strides_for(left_batch);
    const std::vector<int64_t> right_batch_strides = strides_for(right_batch);
    fsv_cuda_index index = {};
    index.rank = static_cast<int>(batch_rank);
    for (int64_t dim = 0; dim < batch_rank; ++dim) {
        index.shape[static_cast<std::size_t>(dim)] = batch_shape[static_cast<std::size_t>(dim)];
        const int64_t left_dim = dim - (batch_rank - static_cast<int64_t>(left_batch.size()));
        const int64_t right_dim = dim - (batch_rank - static_cast<int64_t>(right_batch.size()));
        index.a_stride[static_cast<std::size_t>(dim)] =
            left_dim >= 0 && left_batch[static_cast<std::size_t>(left_dim)] != 1
                ? left_batch_strides[static_cast<std::size_t>(left_dim)] * m * k : 0;
        index.b_stride[static_cast<std::size_t>(dim)] =
            right_dim >= 0 && right_batch[static_cast<std::size_t>(right_dim)] != 1
                ? right_batch_strides[static_cast<std::size_t>(right_dim)] * k * n : 0;
    }
    /* blockIdx.z caps a launch at 65535 slices, so a longer stack is chunked. */
    const int64_t chunk = batch_size > 65535 ? 65535 : batch_size;
    for (int64_t first = 0; first < batch_size; first += chunk) {
        const int64_t remaining = batch_size - first;
        if (fsv_cuda_matmul_f32_batched(left, right, output.device->pointer,
                                        static_cast<int>(m), static_cast<int>(k),
                                        static_cast<int>(n),
                                        remaining < chunk ? remaining : chunk, first,
                                        &index) != 0) {
            output = Tensor();
            return device_decline(inputs);
        }
    }
    layout_shape_note("matmul m=" + std::to_string(m) + " k=" + std::to_string(k) +
                      " n=" + std::to_string(n) + " batch=" + std::to_string(batch_size));
    return true;
}

/* out = source * factor. The factor travels as a kernel argument, so no
   one-element tensor is built, uploaded and broadcast for every Gemm node. */
bool device_scale_into(const Tensor& source, float factor, Tensor& output) {
    if (!cuda_host_enabled() || source.dtype != 1) return false;
    const int64_t count = source.numel();
    if (count <= 0) return false;
    Tensor scaled = device_tensor(source.shape, 1);
    if (!adopt_device_output(scaled)) return false;
    const fsv_cuda_ptr value = ensure_device(const_cast<Tensor&>(source));
    if (!value || fsv_cuda_scale_into_f32(value, scaled.device->pointer,
                                          static_cast<std::size_t>(count), factor) != 0) {
        return false;
    }
    output = std::move(scaled);
    return true;
}

/* out = source * factor, in place. */
bool device_scaled_into(Tensor& source, float factor) {
    Tensor scaled;
    if (!device_scale_into(source, factor, scaled)) return false;
    source = std::move(scaled);
    return true;
}

/* out = source * factor, with the factor a host scalar. */
bool device_scaled(const Tensor& source, float factor, Tensor& output) {
    return device_scale_into(source, factor, output);
}

/* Reduce over a run of consecutive axes: [outer][mid][inner] -> [outer][inner].
   Only a contiguous run can use the block kernel, so an empty axis list or
   axes that skip a dimension stays on the CPU. */
bool device_reduce_block(const Tensor& source, const std::vector<int64_t>& raw_axes,
                         bool keepdims, int operation, Tensor& output) {
    if (!cuda_host_enabled() || source.dtype != 1 || raw_axes.empty()) return false;
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0 || rank > 8) return false;
    std::vector<int64_t> axes = raw_axes;
    for (int64_t& axis : axes) {
        if (axis < 0) axis += rank;
    }
    std::sort(axes.begin(), axes.end());
    for (std::size_t index = 0; index < axes.size(); ++index) {
        if (axes[index] < 0 || axes[index] >= rank) return false;
        if (index && axes[index] != axes[index - 1] + 1) return false;
    }
    long long outer = 1;
    long long mid = 1;
    long long inner = 1;
    for (int64_t dim = 0; dim < axes.front(); ++dim) {
        outer *= source.shape[static_cast<std::size_t>(dim)];
    }
    for (int64_t dim = axes.front(); dim <= axes.back(); ++dim) {
        mid *= source.shape[static_cast<std::size_t>(dim)];
    }
    for (int64_t dim = axes.back() + 1; dim < rank; ++dim) {
        inner *= source.shape[static_cast<std::size_t>(dim)];
    }
    if (outer <= 0 || mid <= 0 || inner <= 0) return false;
    std::vector<int64_t> output_shape;
    for (int64_t dim = 0; dim < rank; ++dim) {
        const bool reduced = std::binary_search(axes.begin(), axes.end(), dim);
        if (!reduced || keepdims) {
            output_shape.push_back(reduced ? 1 : source.shape[static_cast<std::size_t>(dim)]);
        }
    }
    Tensor reduced = device_tensor(output_shape, 1);
    if (!adopt_device_output(reduced)) return false;
    const fsv_cuda_ptr value = ensure_device(const_cast<Tensor&>(source));
    if (!value || fsv_cuda_reduce_block_f32(value, reduced.device->pointer, outer, mid, inner,
                                            operation) != 0) {
        return false;
    }
    output = std::move(reduced);
    return true;
}

bool device_gemm(const std::vector<Tensor*>& inputs, float alpha, float beta,
                 bool transpose_a, bool transpose_b, Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    if (inputs.size() > 2 && inputs[2] && inputs[2]->dtype != 1) return device_decline(inputs);
    if (inputs[0]->shape.size() != 2 || inputs[1]->shape.size() != 2) return device_decline(inputs);
    Tensor transposed_left;
    Tensor transposed_right;
    Tensor scaled_bias;
    Tensor* left = inputs[0];
    Tensor* right = inputs[1];
    Tensor* bias = inputs.size() > 2 && inputs[2] ? inputs[2] : nullptr;
    const std::vector<int64_t> swap{1, 0};
    if (transpose_a) {
        if (!device_transpose(std::vector<Tensor*>{inputs[0]}, swap, transposed_left)) {
            return device_decline(inputs);
        }
        left = &transposed_left;
    }
    if (transpose_b) {
        if (!device_transpose(std::vector<Tensor*>{inputs[1]}, swap, transposed_right)) {
            return device_decline(inputs);
        }
        right = &transposed_right;
    }
    /* C is scaled first so the rest of the path is a plain matrix product
       followed by one broadcast add. */
    if (bias && beta != 1.0f) {
        if (!device_scaled(*bias, beta, scaled_bias)) return device_decline(inputs);
        bias = &scaled_bias;
    }
    Tensor product;
    if (!device_matmul(std::vector<Tensor*>{left, right}, product)) return device_decline(inputs);
    if (alpha != 1.0f && !device_scaled_into(product, alpha)) return device_decline(inputs);
    if (bias) {
        Tensor shifted;
        if (!device_binary(std::vector<Tensor*>{&product, bias}, 0, shifted)) {
            return device_decline(inputs);
        }
        product = std::move(shifted);
    }
    output = std::move(product);
    return true;
}

bool device_matmul_nbits(const std::vector<Tensor*>& inputs, int64_t k, int64_t n,
                         int64_t block_size, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    if (inputs.size() < 3 || !inputs[1] || !inputs[2]) return device_decline(inputs);
    if (inputs[1]->dtype != 2 || (inputs[2]->dtype != 1 && inputs[2]->dtype != 10)) {
        return device_decline(inputs);
    }
    const Tensor& activation = *inputs[0];
    if (k <= 0 || n <= 0 || block_size <= 0 || activation.shape.empty() ||
        activation.shape.back() != k) {
        return device_decline(inputs);
    }
    const int64_t m = activation.numel() / k;
    if (m <= 0) return device_decline(inputs);
    std::vector<int64_t> shape(activation.shape.begin(), activation.shape.end() - 1);
    shape.push_back(n);
    output = device_tensor(shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr a = ensure_device(*inputs[0]);
    const fsv_cuda_ptr packed = ensure_device(*inputs[1]);
    const fsv_cuda_ptr scales = ensure_device(*inputs[2]);
    if (!a || !packed || !scales ||
        fsv_cuda_matmul_nbits_dev(a, packed, scales, output.device->pointer,
                                  static_cast<int>(m), static_cast<int>(k),
                                  static_cast<int>(n), static_cast<int>(block_size), 4,
                                  inputs[2]->dtype == 10 ? 1 : 0) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    layout_shape_note("nbits m=" + std::to_string(m) + " k=" + std::to_string(k) +
                      " n=" + std::to_string(n) + " block=" + std::to_string(block_size));
    return true;
}

bool device_conv(const std::vector<Tensor*>& inputs, const std::vector<int64_t>& pads,
                 const std::vector<int64_t>& strides, const std::vector<int64_t>& dilations,
                 int64_t groups, Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    if (inputs.size() > 2 && inputs[2] && inputs[2]->dtype != 1) return device_decline(inputs);
    const Tensor& input = *inputs[0];
    const Tensor& weight = *inputs[1];
    if (groups <= 0 || input.shape.size() != weight.shape.size() ||
        (input.shape.size() != 4 && input.shape.size() != 3)) {
        return device_decline(inputs);
    }
    const int64_t batch = input.shape[0];
    const int64_t channels = input.shape[1];
    const bool flat = input.shape.size() == 3;
    const int64_t height = flat ? 1 : input.shape[2];
    const int64_t width = flat ? input.shape[2] : input.shape[3];
    const int64_t out_channels = weight.shape[0];
    const int64_t kernel_h = flat ? 1 : weight.shape[2];
    /* A 1-D weight is [out, in/groups, kernel]; a 2-D one is [out, in/groups, kh, kw]. */
    const int64_t kernel_w = flat ? weight.shape[2] : weight.shape[3];
    const int64_t pad_top = flat ? 0 : (pads.empty() ? 0 : pads[0]);
    const int64_t pad_left = flat ? (pads.empty() ? 0 : pads[0])
        : (pads.size() < 2 ? pad_top : pads[1]);
    const int64_t pad_bottom = flat ? 0 : (pads.size() < 3 ? pad_top : pads[2]);
    const int64_t pad_right = flat ? (pads.size() < 2 ? pad_left : pads[1])
        : (pads.size() < 4 ? pad_left : pads[3]);
    const int64_t stride_h = flat ? 1 : (strides.empty() ? 1 : strides[0]);
    const int64_t stride_w = flat ? (strides.empty() ? 1 : strides[0])
        : (strides.size() < 2 ? stride_h : strides[1]);
    const int64_t dilation_h = flat ? 1 : (dilations.empty() ? 1 : dilations[0]);
    const int64_t dilation_w = flat ? (dilations.empty() ? 1 : dilations[0])
        : (dilations.size() < 2 ? dilation_h : dilations[1]);
    if (batch <= 0 || channels <= 0 || width <= 0 || out_channels <= 0 || kernel_w <= 0 ||
        stride_h <= 0 || stride_w <= 0 || dilation_h <= 0 || dilation_w <= 0 ||
        channels % groups || out_channels % groups) {
        return device_decline(inputs);
    }
    const int64_t out_height = (height + pad_top + pad_bottom -
                                (kernel_h - 1) * dilation_h - 1) / stride_h + 1;
    const int64_t out_width = (width + pad_left + pad_right -
                               (kernel_w - 1) * dilation_w - 1) / stride_w + 1;
    if (out_height <= 0 || out_width <= 0) return device_decline(inputs);
    /* A 1-D convolution keeps the rank of its input, exactly like the
       interpreter's conv1d_tensor. */
    output = flat
        ? device_tensor({batch, out_channels, out_width}, 1)
        : device_tensor({batch, out_channels, out_height, out_width}, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr activation = ensure_device(*inputs[0]);
    const fsv_cuda_ptr kernel = ensure_device(*inputs[1]);
    const bool has_bias = inputs.size() > 2 && inputs[2];
    const fsv_cuda_ptr bias = has_bias ? ensure_device(*inputs[2]) : 0;
    if (!activation || !kernel || (has_bias && !bias) ||
        fsv_cuda_conv2d_dev(activation, kernel, bias, output.device->pointer,
                            static_cast<int>(batch), static_cast<int>(channels),
                            static_cast<int>(height), static_cast<int>(width),
                            static_cast<int>(out_channels), static_cast<int>(kernel_h),
                            static_cast<int>(kernel_w), static_cast<int>(pad_top),
                            static_cast<int>(pad_left), static_cast<int>(pad_bottom),
                            static_cast<int>(pad_right), static_cast<int>(stride_h),
                            static_cast<int>(stride_w), static_cast<int>(dilation_h),
                            static_cast<int>(dilation_w), static_cast<int>(groups)) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    layout_shape_note("conv in " + shape_text(input.shape) + " w " +
                      shape_text(weight.shape) + " groups=" + std::to_string(groups) +
                      " stride=" + std::to_string(stride_w));
    return true;
}

bool device_conv_transpose(const std::vector<Tensor*>& inputs,
                           const std::vector<int64_t>& pads,
                           const std::vector<int64_t>& strides,
                           const std::vector<int64_t>& dilations,
                           const std::vector<int64_t>& output_padding, int64_t groups,
                           Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    if (inputs.size() > 2 && inputs[2] && inputs[2]->dtype != 1) return device_decline(inputs);
    const Tensor& input = *inputs[0];
    const Tensor& weight = *inputs[1];
    if (input.shape.size() != 3 || weight.shape.size() != 3 || groups <= 0) {
        return device_decline(inputs);
    }
    const int64_t batch = input.shape[0];
    const int64_t channels = input.shape[1];
    const int64_t width = input.shape[2];
    const int64_t out_channels = weight.shape[1] * groups;
    const int64_t kernel = weight.shape[2];
    const int64_t pad_left = pads.empty() ? 0 : pads[0];
    const int64_t pad_right = pads.size() < 2 ? pad_left : pads[1];
    const int64_t stride = strides.empty() ? 1 : strides[0];
    const int64_t dilation = dilations.empty() ? 1 : dilations[0];
    const int64_t padding = output_padding.empty() ? 0 : output_padding[0];
    if (batch <= 0 || channels <= 0 || width <= 0 || out_channels <= 0 || kernel <= 0 ||
        stride <= 0 || dilation <= 0 || padding < 0 || padding >= stride ||
        channels % groups || out_channels % groups) {
        return device_decline(inputs);
    }
    const int64_t out_width = (width - 1) * stride - pad_left - pad_right +
                              dilation * (kernel - 1) + padding + 1;
    if (out_width <= 0) return device_decline(inputs);
    output = device_tensor({batch, out_channels, out_width}, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr activation = ensure_device(*inputs[0]);
    const fsv_cuda_ptr kernel_ptr = ensure_device(*inputs[1]);
    const bool has_bias = inputs.size() > 2 && inputs[2];
    const fsv_cuda_ptr bias = has_bias ? ensure_device(*inputs[2]) : 0;
    if (!activation || !kernel_ptr || (has_bias && !bias) ||
        fsv_cuda_conv_transpose1d_dev(activation, kernel_ptr, bias, output.device->pointer,
                                      static_cast<int>(batch), static_cast<int>(channels),
                                      static_cast<int>(width), static_cast<int>(out_channels),
                                      static_cast<int>(kernel), static_cast<int>(pad_left),
                                      static_cast<int>(pad_right), static_cast<int>(stride),
                                      static_cast<int>(dilation), static_cast<int>(groups),
                                      static_cast<int>(padding)) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_softmax(const std::vector<Tensor*>& inputs, int64_t axis, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0) return device_decline(inputs);
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank) return device_decline(inputs);
    const int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + axis));
    const int64_t width = source.shape[static_cast<std::size_t>(axis)];
    const int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + axis + 1, source.shape.end()));
    if (inner != 1 || outer <= 0 || width <= 0) return device_decline(inputs);
    output = device_tensor(source.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    if (!value || fsv_cuda_softmax_dev(value, output.device->pointer,
                                       static_cast<int>(outer),
                                       static_cast<int>(width)) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_layer_norm(const std::vector<Tensor*>& inputs, int64_t axis, float epsilon,
                       Tensor& output) {
    if (!device_ready(inputs, 2)) return device_decline(inputs);
    if (inputs.size() > 2 && inputs[2] && inputs[2]->dtype != 1) return device_decline(inputs);
    const Tensor& input = *inputs[0];
    const int64_t rank = static_cast<int64_t>(input.shape.size());
    if (axis < -rank || axis > rank) return device_decline(inputs);
    if (axis < 0) axis += rank;
    if (axis < 0 || axis > rank) return device_decline(inputs);
    const int64_t rows = numel(std::vector<int64_t>(
        input.shape.begin(), input.shape.begin() + axis));
    const int64_t width = numel(std::vector<int64_t>(
        input.shape.begin() + axis, input.shape.end()));
    if (rows <= 0 || width <= 0 || rows > 2147483647 || width > 2147483647) {
        return device_decline(inputs);
    }
    output = device_tensor(input.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const fsv_cuda_ptr scale = ensure_device(*inputs[1]);
    const bool has_bias = inputs.size() > 2 && inputs[2];
    const fsv_cuda_ptr bias = has_bias ? ensure_device(*inputs[2]) : 0;
    if (!value || !scale || (has_bias && !bias) ||
        fsv_cuda_layernorm_dev(value, scale, bias, output.device->pointer,
                               static_cast<int>(rows), static_cast<int>(width),
                               epsilon) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

bool device_batch_norm(const std::vector<Tensor*>& inputs, float epsilon, Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    if (inputs.size() < 5 || !inputs[1] || !inputs[2] || !inputs[3] || !inputs[4]) {
        return device_decline(inputs);
    }
    for (std::size_t index = 1; index < 5; ++index) {
        if (inputs[index]->dtype != 1) return device_decline(inputs);
    }
    const Tensor& input = *inputs[0];
    if (input.shape.size() < 2) return device_decline(inputs);
    const int64_t batch = input.shape[0];
    const int64_t channels = input.shape[1];
    const int64_t spatial = numel(std::vector<int64_t>(
        input.shape.begin() + 2, input.shape.end()));
    if (batch <= 0 || channels <= 0 || spatial <= 0) return device_decline(inputs);
    /* The per-channel statistics are folded on the host: a few hundred scalars
       instead of a second pass over the activation. */
    Tensor folded_scale = make_tensor({channels}, 1);
    Tensor folded_shift = make_tensor({channels}, 1);
    for (int64_t channel = 0; channel < channels; ++channel) {
        const std::size_t index = static_cast<std::size_t>(channel);
        const double mean = read_numeric(*inputs[3], index);
        const double variance = read_numeric(*inputs[4], index);
        const double factor = read_numeric(*inputs[1], index) / std::sqrt(variance + epsilon);
        write_numeric(folded_scale, index, factor);
        write_numeric(folded_shift, index, read_numeric(*inputs[2], index) - mean * factor);
    }
    output = device_tensor(input.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const fsv_cuda_ptr scale = ensure_device(folded_scale);
    const fsv_cuda_ptr shift = ensure_device(folded_shift);
    if (!value || !scale || !shift ||
        fsv_cuda_channel_affine_f32(value, scale, shift, output.device->pointer,
                                    batch, channels, spatial) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* Pad in constant mode is a fill of the result plus one strided copy of the
   operand into the box it occupies. The host version pulled the operand back
   and walked every output element, and vits_v2pro evaluates 97 of them for its
   attention masks, so each one was a queue drain for a few hundred kilobytes.
   The other modes (edge, wrap, reflect) still take the host path: they read
   neighbouring elements, which the copy's coordinate walk cannot express. */
bool device_pad(const std::vector<Tensor*>& inputs, const std::vector<int64_t>& pads,
                float value, const std::string& mode, Tensor& output) {
    if (!mode.empty() && mode != "constant") return device_decline(inputs);
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0) return device_decline(inputs);
    if (pads.size() != static_cast<std::size_t>(rank) * 2) return device_decline(inputs);
    std::vector<int64_t> output_shape(source.shape);
    for (int64_t dim = 0; dim < rank; ++dim) {
        const int64_t begin = pads[static_cast<std::size_t>(dim)];
        const int64_t end = pads[static_cast<std::size_t>(dim + rank)];
        if (begin < 0 || end < 0) return device_decline(inputs);
        output_shape[static_cast<std::size_t>(dim)] += begin + end;
    }
    const int64_t count = source.numel();
    if (count <= 0) return device_decline(inputs);
    output = device_tensor(output_shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const std::vector<int64_t> source_strides = strides_for(source.shape);
    const std::vector<int64_t> output_strides = strides_for(output_shape);
    fsv_cuda_index index = {};
    int64_t destination_base = 0;
    for (int64_t dim = 0; dim < rank; ++dim) {
        const std::size_t slot = static_cast<std::size_t>(dim);
        index.shape[slot] = source.shape[slot];
        index.a_stride[slot] = source_strides[slot];
        index.b_stride[slot] = output_strides[slot];
        destination_base += pads[slot] * output_strides[slot];
    }
    index.rank = static_cast<int>(rank);
    const fsv_cuda_ptr operand = ensure_device(*inputs[0]);
    const fsv_cuda_ptr destination = output.device->pointer;
    if (!operand ||
        fsv_cuda_fill_f32(destination, static_cast<std::size_t>(output.numel()), value) != 0 ||
        fsv_cuda_copy_nd_f32(operand, destination, static_cast<std::size_t>(count), 0,
                             destination_base, static_cast<int>(rank), &index) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* ArgMax is the token decision of the decode loop: the host version read the
   logits back to find one index, which drained the queue 126 times a synthesis
   for a scalar. The scan order matches the host reference, so a tie keeps the
   lowest index. */
bool device_argmax(const std::vector<Tensor*>& inputs, int64_t axis, bool keepdims,
                   Tensor& output) {
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    const Tensor& source = *inputs[0];
    const int64_t rank = static_cast<int64_t>(source.shape.size());
    if (rank <= 0) return device_decline(inputs);
    if (axis < 0) axis += rank;
    if (axis < 0 || axis >= rank) return device_decline(inputs);
    std::vector<int64_t> output_shape = source.shape;
    if (keepdims) {
        output_shape[static_cast<std::size_t>(axis)] = 1;
    } else {
        output_shape.erase(output_shape.begin() + axis);
    }
    const int64_t outer = numel(std::vector<int64_t>(
        source.shape.begin(), source.shape.begin() + axis));
    const int64_t width = source.shape[static_cast<std::size_t>(axis)];
    const int64_t inner = numel(std::vector<int64_t>(
        source.shape.begin() + axis + 1, source.shape.end()));
    if (outer <= 0 || width <= 0 || inner <= 0) return device_decline(inputs);
    output = device_tensor(output_shape, 7);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr values = ensure_device(*inputs[0]);
    if (!values || fsv_cuda_argmax_i64(values, output.device->pointer, outer, width,
                                       inner) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}

/* InstanceNormalization reduces over the trailing dimensions of
   [batch][channels][spatial...]. The host version read the whole activation
   back and folded nine million elements one at a time; one block per
   (batch, channel) row keeps both passes on the card. */
bool device_instance_norm(const std::vector<Tensor*>& inputs, float epsilon,
                          Tensor& output) {
    if (inputs.size() < 3 || !inputs[1] || !inputs[2]) return device_decline(inputs);
    if (!device_ready(inputs, 1)) return device_decline(inputs);
    if (inputs[1]->dtype != 1 || inputs[2]->dtype != 1) return device_decline(inputs);
    const Tensor& input = *inputs[0];
    if (input.shape.size() < 3) return device_decline(inputs);
    const int64_t batch = input.shape[0];
    const int64_t channels = input.shape[1];
    const int64_t spatial = numel(std::vector<int64_t>(
        input.shape.begin() + 2, input.shape.end()));
    if (batch <= 0 || channels <= 0 || spatial <= 0) return device_decline(inputs);
    if (inputs[1]->numel() < channels || inputs[2]->numel() < channels) {
        return device_decline(inputs);
    }
    output = device_tensor(input.shape, 1);
    if (!adopt_device_output(output)) return device_decline(inputs);
    const fsv_cuda_ptr value = ensure_device(*inputs[0]);
    const fsv_cuda_ptr scale = ensure_device(*inputs[1]);
    const fsv_cuda_ptr bias = ensure_device(*inputs[2]);
    if (!value || !scale || !bias ||
        fsv_cuda_instance_norm_f32(value, scale, bias, output.device->pointer,
                                   batch * channels, channels, spatial,
                                   epsilon) != 0) {
        output = Tensor();
        return device_decline(inputs);
    }
    return true;
}
std::vector<int64_t> axes_from_input(const Value& value, const fsv::AttributeProto* attr) {
    if (value.tensor) {
        std::vector<int64_t> axes;
        for (int64_t index = 0; index < value.tensor->numel(); ++index) {
            axes.push_back(read_scalar<int64_t>(*value.tensor, static_cast<std::size_t>(index)));
        }
        return axes;
    }
    return attr ? attr->ints : std::vector<int64_t>{};
}

bool get_tensor(const std::unordered_map<std::string, Value>& values,
                const std::string& name, Tensor& output) {
    auto iterator = values.find(name);
    if (iterator == values.end() || !iterator->second.tensor) return false;
    output = *iterator->second.tensor;
    return true;
}

/* Constant nodes are the same bytes on every run, and the exported graphs
   carry hundreds of them per decoder step: re-materialising them 61 times was
   a quarter of every node the graph executed. Folding them into the
   initializer table once also puts them on the weight path, where a device
   copy is looked up instead of uploaded. Subgraphs are folded too, since a
   Loop body or an If branch reads its constants from the enclosing scope. */
void fold_constants(fsv::GraphProto& graph,
                    std::unordered_map<std::string, fsv::Tensor>& constants) {
    for (fsv::NodeProto& node : graph.nodes) {
        for (fsv::AttributeProto& attribute : node.attributes) {
            if (attribute.graph) fold_constants(*attribute.graph, constants);
            for (const std::shared_ptr<fsv::GraphProto>& nested : attribute.graphs) {
                if (nested) fold_constants(*nested, constants);
            }
        }
    }
    std::vector<fsv::NodeProto> kept;
    kept.reserve(graph.nodes.size());
    for (fsv::NodeProto& node : graph.nodes) {
        const fsv::AttributeProto* value =
            node.op_type == "Constant" ? find_attr(node, "value") : nullptr;
        if (!value || node.outputs.size() != 1 || node.outputs[0].empty()) {
            kept.push_back(std::move(node));
            continue;
        }
        /* An initializer of the same name is the more specific declaration. */
        constants.emplace(node.outputs[0], tensor_from_proto(value->tensor));
    }
    graph.nodes = std::move(kept);
}

}  // namespace

namespace fsv {

int64_t Tensor::numel() const {
    return ::numel(shape);
}

std::size_t Tensor::byte_size() const {
    /* Derived from the shape instead of the payload. A CUDA operator leaves its
       result on the card without ever allocating host bytes, and every consumer
       still has to know how large that value is. */
    return static_cast<std::size_t>(::numel(shape)) * element_size(dtype);
}

GraphRuntime::GraphRuntime(ModelProto model, std::string model_dir,
                           std::string external_weights)
    : model_(std::move(model)), model_dir_(std::move(model_dir)),
      external_weights_path_(std::move(external_weights)) {}

/* A slot index that means "this node leaves the operand out", the same thing
   ``node.inputs`` says with an empty name. Also used as the read count of a
   value that must never be released. */
const std::size_t kNoValueSlot = std::numeric_limits<std::size_t>::max();

struct GraphRuntime::Plan {
    /* One entry per name the model can mention, in reset order. The pointers
       stay valid for the life of the runtime: a name is inserted once and
       never erased, and erasing is the only thing that moves an element of an
       unordered_map. */
    std::vector<Value*> slots;
    /* Weights, paired with their slot, so binding them costs a pointer store
       instead of a lookup per weight. */
    std::vector<std::pair<Value*, const Tensor*>> initializers;
    /* Operand and result slots, flattened by node. */
    std::vector<std::size_t> node_inputs;
    std::vector<std::size_t> input_offset;
    std::vector<std::size_t> node_outputs;
    std::vector<std::size_t> output_offset;
    /* Reads per slot, with graph outputs pinned so the release pass cannot
       drop a value the caller still reads. */
    std::vector<std::size_t> uses;
};

/* Every name a graph can put into the enclosing value table. An If branch or a
   Loop body writes its own outputs into the scope it runs in, so those names
   have to be in the table too. */
void collect_value_names(const fsv::GraphProto& graph, std::vector<std::string>& names) {
    for (const auto& node : graph.nodes) {
        for (const std::string& name : node.inputs) {
            if (!name.empty()) names.push_back(name);
        }
        for (const std::string& name : node.outputs) {
            if (!name.empty()) names.push_back(name);
        }
        for (const auto& attribute : node.attributes) {
            if (attribute.graph) collect_value_names(*attribute.graph, names);
            for (const auto& nested : attribute.graphs) {
                if (nested) collect_value_names(*nested, names);
            }
        }
    }
}

/* Same walk as count_value_uses(), but charging the reads to slots. */
void collect_read_counts(const fsv::GraphProto& graph,
                         const std::unordered_map<std::string, std::size_t>& slot_of,
                         std::vector<std::size_t>& uses) {
    for (const auto& node : graph.nodes) {
        for (const std::string& name : node.inputs) {
            if (name.empty()) continue;
            auto found = slot_of.find(name);
            if (found != slot_of.end()) ++uses[found->second];
        }
        for (const auto& attribute : node.attributes) {
            if (attribute.graph) collect_read_counts(*attribute.graph, slot_of, uses);
            for (const auto& nested : attribute.graphs) {
                if (nested) collect_read_counts(*nested, slot_of, uses);
            }
        }
    }
}

bool GraphRuntime::build_plan(std::string& error) {
    (void)error;
    std::unique_ptr<Plan> plan(new Plan());
    std::vector<std::string> names;
    names.reserve(initializers_.size() + model_.graph.nodes.size() * 4);
    for (const auto& item : initializers_) names.push_back(item.first);
    for (const ValueInfo& info : model_.graph.inputs) names.push_back(info.name);
    for (const ValueInfo& info : model_.graph.outputs) names.push_back(info.name);
    collect_value_names(model_.graph, names);
    std::sort(names.begin(), names.end());
    names.erase(std::unique(names.begin(), names.end()), names.end());

    std::unordered_map<std::string, std::size_t> slot_of;
    slot_of.reserve(names.size() * 2);
    values_.reserve(names.size() * 2);
    plan->slots.reserve(names.size());
    for (const std::string& name : names) {
        slot_of.emplace(name, plan->slots.size());
        plan->slots.push_back(&values_[name]);
    }
    plan->uses.assign(plan->slots.size(), 0);
    collect_read_counts(model_.graph, slot_of, plan->uses);
    for (const ValueInfo& info : model_.graph.outputs) {
        auto found = slot_of.find(info.name);
        if (found != slot_of.end()) plan->uses[found->second] = kNoValueSlot;
    }
    plan->initializers.reserve(initializers_.size());
    for (const auto& item : initializers_) {
        auto found = slot_of.find(item.first);
        if (found == slot_of.end()) continue;
        plan->initializers.emplace_back(plan->slots[found->second], &item.second);
    }

    const std::vector<fsv::NodeProto>& nodes = model_.graph.nodes;
    plan->input_offset.reserve(nodes.size() + 1);
    plan->output_offset.reserve(nodes.size() + 1);
    plan->node_inputs.reserve(nodes.size() * 2);
    plan->node_outputs.reserve(nodes.size() * 2);
    for (const fsv::NodeProto& node : nodes) {
        plan->input_offset.push_back(plan->node_inputs.size());
        for (const std::string& name : node.inputs) {
            auto found = name.empty() ? slot_of.end() : slot_of.find(name);
            plan->node_inputs.push_back(found == slot_of.end() ? kNoValueSlot
                                                              : found->second);
        }
        plan->output_offset.push_back(plan->node_outputs.size());
        for (const std::string& name : node.outputs) {
            auto found = name.empty() ? slot_of.end() : slot_of.find(name);
            plan->node_outputs.push_back(found == slot_of.end() ? kNoValueSlot
                                                               : found->second);
        }
    }
    plan->input_offset.push_back(plan->node_inputs.size());
    plan->output_offset.push_back(plan->node_outputs.size());
    plan_ = std::move(plan);
    return true;
}

bool GraphRuntime::prepare(std::string& error) {
    initializers_.clear();
    external_files_.clear();
    /* End of the fp32 layout each file's metadata describes, per file. */
    std::unordered_map<std::string, int64_t> external_extent;
    for (const TensorProto& proto : model_.graph.initializers) {
        if (proto.data_location != 1) continue;
        std::string path = external_weights_path_;
        if (path.empty() && !proto.external_location.empty()) {
            path = model_dir_ + "/" + proto.external_location;
        }
        int64_t offset = 0;
        int64_t length = 0;
        parse_int64(proto.external_offset, offset);
        parse_int64(proto.external_length, length);
        int64_t& extent = external_extent[path];
        if (offset + length > extent) extent = offset + length;
    }
    for (const TensorProto& proto : model_.graph.initializers) {
        Tensor tensor;
        if (proto.data_location == 1) {
            std::string path = external_weights_path_;
            if (path.empty() && !proto.external_location.empty()) {
                path = model_dir_ + "/" + proto.external_location;
            }
            auto iterator = external_files_.find(path);
            if (iterator == external_files_.end()) {
                std::string raw = read_file(path, error);
                if (!error.empty()) return false;
                iterator = external_files_.emplace(path, std::move(raw)).first;
            }
            const bool packed =
                static_cast<int64_t>(iterator->second.size()) < external_extent[path];
            if (!load_external_weights(iterator->second, proto, packed, tensor, error)) {
                return false;
            }
        } else if (!load_initializer(proto, tensor, error)) {
            return false;
        }
    initializers_[proto.name] = std::move(tensor);
    }
    fold_constants(model_.graph, initializers_);
    /* Initializers live for the whole graph, so their device copies can too:
       registering them here is what keeps weight uploads out of the node loop. */
    if (std::getenv("FSV_NATIVE_CPU_ONLY") == nullptr && fsv_cuda_device_count() > 0) {
        /* Charged under one name so [op-stats] shows how much of the traffic is
           one-off weight residency rather than per-node graph work. */
        fsv::opstats::Scope weights_scope("(weights)");
        for (const auto& item : initializers_) {
            fsv_cuda_constant_register(item.second.data.data(), item.second.data.size());
        }
        constants_registered_ = true;
    }
    return build_plan(error);
}

GraphRuntime::~GraphRuntime() {
    if (!constants_registered_) return;
    for (const auto& item : initializers_) {
        fsv_cuda_constant_unregister(item.second.data.data());
    }
}

std::vector<std::string> GraphRuntime::output_names() const {
    std::vector<std::string> names;
    names.reserve(model_.graph.outputs.size());
    for (const ValueInfo& info : model_.graph.outputs) names.push_back(info.name);
    return names;
}

std::vector<std::string> GraphRuntime::input_names() const {
    std::vector<std::string> names;
    names.reserve(model_.graph.inputs.size());
    for (const ValueInfo& info : model_.graph.inputs) names.push_back(info.name);
    return names;
}

const Tensor* GraphRuntime::find_captured(const std::string& name) const {
    auto iterator = captured_.find(name);
    if (iterator == captured_.end()) return nullptr;
    /* The comparison tools read host bytes, and this tensor may only exist on
       the device. */
    ensure_host(iterator->second);
    return &iterator->second;
}

std::vector<std::string> GraphRuntime::captured_names() const {
    std::vector<std::string> names;
    names.reserve(captured_.size());
    for (const auto& item : captured_) names.push_back(item.first);
    std::sort(names.begin(), names.end());
    return names;
}

void GraphRuntime::set_resident(
    const std::vector<std::pair<std::string, std::string>>& pairs) {
    resident_.clear();
    resident_feeder_.clear();
    resident_names_.clear();
    for (const auto& pair : pairs) {
        resident_feeder_[pair.second] = pair.first;
        resident_names_.push_back(pair.first);
    }
}

bool GraphRuntime::resident_output(const std::string& name) const {
    return std::find(resident_names_.begin(), resident_names_.end(), name) !=
           resident_names_.end();
}

const Tensor* GraphRuntime::find_resident(const std::string& name) const {
    auto iterator = resident_.find(name);
    if (iterator == resident_.end()) return nullptr;
    ensure_host(iterator->second);
    return &iterator->second;
}

bool GraphRuntime::run(const std::unordered_map<std::string, Tensor>& feeds,
                       std::unordered_map<std::string, Tensor>& outputs,
                       std::string& error) {
    /* A new run gets a fresh chance at the card: the memory a previous run was
       refused may have been released since (another process exiting, a package
       swap), and abandoning the device for the rest of the process would turn a
       transient shortage into a permanent slowdown. */
    fsv_cuda_reset_device_abandoned();
    const bool trace = std::getenv("FSV_NATIVE_TRACE") != nullptr;
    /* The value table belongs to the runtime, not to the run: every slot is
       emptied and filled again, which costs a pointer store per name instead
       of rebuilding, allocating and rehashing the whole table for every decode
       step. */
    std::vector<std::size_t> remaining;
    if (plan_) {
        for (Value* slot : plan_->slots) *slot = Value();
        remaining = plan_->uses;
        for (const auto& item : plan_->initializers) {
            Value value;
            value.tensor = std::make_shared<Tensor>(*item.second);
            *item.first = std::move(value);
        }
    } else {
        values_.clear();
        for (const auto& item : initializers_) {
            Value value;
            value.tensor = std::make_shared<Tensor>(item.second);
            values_[item.first] = std::move(value);
        }
    }
    for (const auto& item : feeds) {
        Value value;
        value.tensor = std::make_shared<Tensor>(item.second);
        values_[item.first] = std::move(value);
    }
    /* A retained output stands in for its input when the caller has no host
       bytes for it, which is the point of retaining it: the decode loop hands
       the cache back without ever materialising it. A caller that does supply
       the input wins, and that is how the first step seeds the cache. */
    for (const auto& item : resident_feeder_) {
        if (feeds.find(item.first) != feeds.end()) continue;
        auto retained = resident_.find(item.second);
        if (retained == resident_.end()) continue;
        Value value;
        value.tensor = std::make_shared<Tensor>(retained->second);
        values_[item.first] = std::move(value);
    }

    /* Which store a node reads from and writes to. The top-level graph uses the
       plan's slots; a subgraph gets a map of its own, which is what an If
       branch or a Loop body needs to read the enclosing scope. */
    struct Scope {
        std::unordered_map<std::string, Value>* map = nullptr;
        const GraphRuntime::Plan* plan = nullptr;
        std::vector<std::size_t>* remaining = nullptr;
    };

    std::function<bool(const GraphProto&, Scope&, std::string&, bool)> execute;
    execute = [&](const GraphProto& graph, Scope& scope,
                  std::string& error, bool top_level) -> bool {
    const bool planned = scope.plan != nullptr;
    const std::vector<std::size_t>* plan_inputs = planned ? &scope.plan->node_inputs : nullptr;
    const std::vector<std::size_t>* plan_input_offset = planned ? &scope.plan->input_offset : nullptr;
    const std::vector<std::size_t>* plan_outputs = planned ? &scope.plan->node_outputs : nullptr;
    const std::vector<std::size_t>* plan_output_offset = planned ? &scope.plan->output_offset : nullptr;
    std::unordered_map<std::string, Value>& values = scope.map ? *scope.map : values_;
    std::unordered_map<std::string, std::size_t> remaining_uses;
    const bool release_unused = top_level && !capture_ && !std::getenv("FSV_NATIVE_CAPTURE");
    if (release_unused && !planned) {
        count_value_uses(graph, remaining_uses);
        /* The caller reads these through the output map once the graph ends. */
        for (const ValueInfo& info : graph.outputs) remaining_uses.erase(info.name);
    }
    for (std::size_t node_index = 0; node_index < graph.nodes.size(); ++node_index) {
        const fsv::NodeProto& node = graph.nodes[node_index];
        if (trace) {
            FSV_TRACE( "[native-run] node %zu %s (%s)\n",
                         node_index, node.op_type.c_str(), node.name.c_str());
        }
        const std::string& op = node.op_type;
        fsv::opstats::Scope op_scope(op.c_str(), node.name.c_str());
        const bool probe = node_probe_enabled();
        const std::chrono::steady_clock::time_point probe_start = probe
            ? std::chrono::steady_clock::now()
            : std::chrono::steady_clock::time_point();
        std::vector<Tensor> inputs;
        /* Points at the tensor held by the value table. The device helpers
           remember residency on it, so a weight is uploaded once per run
           rather than once per node that reads it. */
        std::vector<Tensor*> slots;
        std::vector<Value> input_values;
        const std::size_t input_count = node.inputs.size();
        inputs.reserve(input_count);
        slots.reserve(input_count);
        input_values.reserve(input_count);
        const std::size_t input_base = planned ? (*plan_input_offset)[node_index] : 0;
        for (std::size_t input_index = 0; input_index < input_count; ++input_index) {
            Value value;
            if (planned) {
                const std::size_t slot = (*plan_inputs)[input_base + input_index];
                if (slot != kNoValueSlot) value = *scope.plan->slots[slot];
            } else {
                auto iterator = values.find(node.inputs[input_index]);
                if (iterator != values.end()) value = iterator->second;
            }
            input_values.push_back(value);
            if (value.tensor) {
                slots.push_back(value.tensor.get());
                inputs.push_back(*value.tensor);
            } else {
                slots.push_back(nullptr);
                inputs.emplace_back();
            }
        }
        if (trace) {
            for (std::size_t index = 0; index < inputs.size(); ++index) {
                FSV_TRACE( "[native-run]   in%zu shape=%s dtype=%d\n", index,
                             shape_text(inputs[index].shape).c_str(), inputs[index].dtype);
            }
        }
        const std::chrono::steady_clock::time_point probe_gathered = probe
            ? std::chrono::steady_clock::now()
            : std::chrono::steady_clock::time_point();
        /* Where this node's results go, or null for an optional output the
           graph leaves unnamed. */
        const std::size_t output_base = planned ? (*plan_output_offset)[node_index] : 0;
        auto output_slot = [&](std::size_t index) -> Value* {
            if (!planned) {
                return node.outputs[index].empty() ? nullptr : &values[node.outputs[index]];
            }
            const std::size_t slot = (*plan_outputs)[output_base + index];
            return slot == kNoValueSlot ? nullptr : scope.plan->slots[slot];
        };
        Tensor output;
        std::vector<Tensor> multi_outputs;
        Value sequence_value;
        std::vector<Value> flow_values;
        if (op == "Constant") {
            const fsv::AttributeProto* value_attr = find_attr(node, "value");
            if (!value_attr) {
                error = "Constant without value: " + node.name;
                return false;
            }
            output = tensor_from_proto(value_attr->tensor);
        } else if (op == "ConstantOfShape") {
            std::vector<int64_t> shape;
            for (int64_t index = 0; index < inputs[0].numel(); ++index) {
                shape.push_back(read_scalar<int64_t>(inputs[0], static_cast<std::size_t>(index)));
            }
            FSV_TRACE( "[native-run] ConstantOfShape in=%s value=%s\n",
                         shape_text(inputs[0].shape).c_str(), shape_text(shape).c_str());
            const fsv::AttributeProto* value_attr = find_attr(node, "value");
            Tensor value = value_attr ? tensor_from_proto(value_attr->tensor) : scalar_tensor(1);
            output = make_tensor(shape, value.dtype);
            std::size_t bytes = element_size(value.dtype);
            for (int64_t index = 0; index < output.numel(); ++index) {
                std::memcpy(output.data.data() + static_cast<std::size_t>(index) * bytes,
                            value.data.data(), bytes);
            }
            FSV_TRACE( "[native-run] ConstantOfShape out=%s\n", shape_text(output.shape).c_str());
        } else if (op == "Identity") {
            output = inputs[0];
        } else if (op == "Add" || op == "Sub" || op == "Mul" || op == "Div") {
            int operation = op == "Add" ? 0 : op == "Mul" ? 1 :
                op == "Sub" ? 2 : 3;
            if (!device_binary(slots, operation, output)) {
                output = binary_tensor(inputs[0], inputs[1], operation);
            }
        } else if (op == "Pow") {
            if (!device_binary(slots, 4, output)) {
                output = pow_tensor(inputs[0], inputs[1]);
            }
        } else if (op == "Neg" || op == "Sin" || op == "Cos" || op == "Sqrt" ||
                   op == "Erf" || op == "Tanh" || op == "Sigmoid" || op == "Exp" ||
                   op == "Log" || op == "Softplus" || op == "Relu" ||
                   op == "LeakyRelu" || op == "Floor") {
            int operation = op == "Neg" ? 0 : op == "Sin" ? 1 :
                op == "Cos" ? 2 : op == "Sqrt" ? 3 : op == "Erf" ? 4 :
                op == "Tanh" ? 5 : op == "Sigmoid" ? 6 : op == "Exp" ? 7 :
                op == "Log" ? 8 : op == "Softplus" ? 9 : op == "Relu" ? 10 :
                op == "LeakyRelu" ? 11 : 12;
            float alpha = 0.01f;
            if (const fsv::AttributeProto* alpha_attr = find_attr(node, "alpha")) alpha = alpha_attr->f;
            if (!device_unary(slots, operation, alpha, output)) {
                output = unary_tensor(inputs[0], operation, alpha);
            }
        } else if (op == "ReduceSum") {
            std::vector<int64_t> axes;
            if (node.inputs.size() > 1 && !node.inputs[1].empty() &&
                values.count(node.inputs[1])) {
                axes = axes_from_input(values.at(node.inputs[1]), find_attr(node, "axes"));
            } else {
                axes = find_attr(node, "axes") ? find_attr(node, "axes")->ints
                                               : std::vector<int64_t>{};
            }
            const bool keepdims_sum = attr_int(node, "keepdims", 1) != 0;
            if (!device_reduce_block(inputs[0], axes, keepdims_sum, 0, output)) {
                output = reduce_tensor(inputs[0], axes, keepdims_sum, 0);
            }
        } else if (op == "ReduceMean" || op == "ReduceMax" || op == "ReduceL2") {
            std::vector<int64_t> axes;
            if (node.inputs.size() > 1 && !node.inputs[1].empty() &&
                values.count(node.inputs[1])) {
                axes = axes_from_input(values.at(node.inputs[1]), find_attr(node, "axes"));
            } else {
                axes = find_attr(node, "axes") ? find_attr(node, "axes")->ints
                                                : std::vector<int64_t>{};
            }
            int operation = op == "ReduceMean" ? 1 : op == "ReduceMax" ? 2 : 3;
            const bool keepdims_reduce = attr_int(node, "keepdims", 1) != 0;
            if (!device_reduce_block(inputs[0], axes, keepdims_reduce, operation, output)) {
                output = reduce_tensor(inputs[0], axes, keepdims_reduce, operation);
            }
        } else if (op == "Shape") {
            int64_t start = attr_int(node, "start", 0);
            int64_t end = attr_int(node, "end", static_cast<int64_t>(inputs[0].shape.size()));
            int64_t rank = static_cast<int64_t>(inputs[0].shape.size());
            if (start < 0) start += rank;
            if (end < 0) end += rank;
            start = std::max<int64_t>(0, start);
            end = std::min(rank, end);
            Tensor shape = make_tensor({std::max<int64_t>(0, end - start)}, 7);
            for (int64_t index = start; index < end; ++index) {
                write_scalar<int64_t>(shape, static_cast<std::size_t>(index - start),
                    inputs[0].shape[static_cast<std::size_t>(index)]);
            }
            output = std::move(shape);
        } else if (op == "Reshape") {
            std::vector<int64_t> target;
            for (int64_t index = 0; index < inputs[1].numel(); ++index) {
                target.push_back(read_scalar<int64_t>(inputs[1], static_cast<std::size_t>(index)));
            }
            output = reshape_tensor(inputs[0], target);
        } else if (op == "Transpose") {
            const fsv::AttributeProto* perm = find_attr(node, "perm");
            std::vector<int64_t> permutation;
            if (perm) permutation = perm->ints;
            else {
                for (int64_t index = static_cast<int64_t>(inputs[0].shape.size()) - 1;
                     index >= 0; --index) permutation.push_back(index);
            }
            if (!device_transpose(slots, permutation, output)) {
                output = transpose_tensor(inputs[0], permutation);
            }
        } else if (op == "Unsqueeze") {
            std::vector<int64_t> axes;
            if (node.inputs.size() > 1 && !node.inputs[1].empty() &&
                values.count(node.inputs[1])) {
                axes = axes_from_input(values.at(node.inputs[1]), find_attr(node, "axes"));
            } else {
                axes = find_attr(node, "axes") ? find_attr(node, "axes")->ints
                                               : std::vector<int64_t>{};
            }
            output = unsqueeze_tensor(inputs[0], axes);
        } else if (op == "Squeeze") {
            std::vector<int64_t> axes;
            if (node.inputs.size() > 1 && !node.inputs[1].empty() &&
                values.count(node.inputs[1])) {
                axes = axes_from_input(values.at(node.inputs[1]), find_attr(node, "axes"));
            } else {
                axes = find_attr(node, "axes") ? find_attr(node, "axes")->ints
                                               : std::vector<int64_t>{};
            }
            output = squeeze_tensor(inputs[0], axes);
        } else if (op == "Slice") {
            std::vector<int64_t> starts;
            std::vector<int64_t> ends;
            std::vector<int64_t> axes;
            std::vector<int64_t> steps;
            if (inputs.size() > 1 && inputs[1].dtype) starts = ints_from_tensor(inputs[1]);
            else if (find_attr(node, "starts")) starts = find_attr(node, "starts")->ints;
            if (inputs.size() > 2 && inputs[2].dtype) ends = ints_from_tensor(inputs[2]);
            else if (find_attr(node, "ends")) ends = find_attr(node, "ends")->ints;
            if (inputs.size() > 3 && inputs[3].dtype) axes = ints_from_tensor(inputs[3]);
            else if (find_attr(node, "axes")) axes = find_attr(node, "axes")->ints;
            if (inputs.size() > 4 && inputs[4].dtype) steps = ints_from_tensor(inputs[4]);
            else if (find_attr(node, "steps")) steps = find_attr(node, "steps")->ints;
            if (!device_slice(slots, starts, ends, axes, steps, output)) {
                output = slice_tensor(inputs[0], starts, ends, axes, steps);
            }
        } else if (op == "MatMul") {
            if (!device_matmul(slots, output)) output = matmul_tensor(inputs[0], inputs[1]);
        } else if (op == "Equal") {
            if (!device_compare(slots, 0, output)) output = equal_tensor(inputs[0], inputs[1]);
        } else if (op == "Less" || op == "Greater") {
            if (!device_compare(slots, op == "Less" ? 1 : 2, output)) {
                output = compare_tensor(inputs[0], inputs[1], op == "Less" ? 1 : 2);
            }
        } else if (op == "Not") {
            output = not_tensor(inputs[0]);
        } else if (op == "Or") {
            output = or_tensor(inputs[0], inputs[1]);
        } else if (op == "Where") {
            if (!device_where(slots, output)) {
                output = where_tensor(inputs[0], inputs[1], inputs[2]);
            }
        } else if (op == "Clip") {
            Tensor empty;
            if (!device_clip(slots, output)) {
                output = clip_tensor(inputs[0],
                    inputs.size() > 1 && inputs[1].dtype ? inputs[1] : empty,
                    inputs.size() > 2 && inputs[2].dtype ? inputs[2] : empty);
            }
        } else if (op == "Max") {
            if (!device_max(slots, output)) output = max_tensors(inputs);
        } else if (op == "Expand") {
            output = expand_tensor(inputs[0], ints_from_tensor(inputs[1]));
        } else if (op == "Flatten") {
            output = flatten_tensor(inputs[0], attr_int(node, "axis", 1));
        } else if (op == "Range") {
            output = range_tensor(inputs[0], inputs[1], inputs[2]);
        } else if (op == "Pad") {
            std::vector<int64_t> pads;
            if (inputs.size() > 1 && inputs[1].dtype) pads = ints_from_tensor(inputs[1]);
            else if (find_attr(node, "pads")) pads = find_attr(node, "pads")->ints;
            std::vector<int64_t> axes;
            if (inputs.size() > 3 && inputs[3].dtype) axes = ints_from_tensor(inputs[3]);
            float value = 0.0f;
            if (inputs.size() > 2 && inputs[2].dtype) value = read_scalar<float>(inputs[2]);
            else if (const fsv::AttributeProto* value_attr = find_attr(node, "value")) value = value_attr->f;
            if (!axes.empty() && pads.size() == axes.size() * 2) {
                int64_t rank = static_cast<int64_t>(inputs[0].shape.size());
                std::vector<int64_t> full_pads(static_cast<std::size_t>(rank * 2), 0);
                for (std::size_t index = 0; index < axes.size(); ++index) {
                    int64_t axis = axes[index];
                    if (axis < 0) axis += rank;
                    if (axis < 0 || axis >= rank) return false;
                    full_pads[static_cast<std::size_t>(axis)] = pads[index];
                    full_pads[static_cast<std::size_t>(axis + rank)] =
                        pads[index + axes.size()];
                }
                pads = std::move(full_pads);
            }
            std::string pad_mode = "constant";
            if (const fsv::AttributeProto* mode_attr = find_attr(node, "mode")) {
                pad_mode = mode_attr->s;
            }
            if (!device_pad(slots, pads, value, pad_mode, output)) {
                output = pad_tensor(inputs[0], pads, value, inputs[0].dtype, pad_mode);
            }
            layout_shape_note("pad " + shape_text(inputs[0].shape) + " -> " +
                              shape_text(output.shape) + " mode=" + pad_mode);
        } else if (op == "Resize") {
            std::vector<double> scales;
            std::vector<int64_t> sizes;
            if (inputs.size() > 2 && inputs[2].dtype) {
                for (int64_t index = 0; index < inputs[2].numel(); ++index) {
                    scales.push_back(read_numeric(inputs[2], static_cast<std::size_t>(index)));
                }
            }
            if (inputs.size() > 3 && inputs[3].dtype) sizes = ints_from_tensor(inputs[3]);
            const fsv::AttributeProto* mode_attr = find_attr(node, "mode");
            const fsv::AttributeProto* coord_attr = find_attr(node, "coordinate_transformation_mode");
            const fsv::AttributeProto* nearest_attr = find_attr(node, "nearest_mode");
            output = resize_tensor(inputs[0], scales, sizes,
                mode_attr ? mode_attr->s : "nearest",
                coord_attr ? coord_attr->s : "half_pixel",
                nearest_attr ? nearest_attr->s : "round_prefer_floor");
        } else if (op == "Concat") {
            int64_t axis = attr_int(node, "axis", 0);
            if ((node.name == "GsvFeatureSizes" || node.name == "GsvMaskSizes") && axis == 0) {
                output = concat_shape_vector_tensors(inputs);
            } else if (!device_concat(slots, axis, output)) {
                output = concat_tensors(inputs, axis);
            }
        } else if (op == "Gather") {
            if (!device_gather(slots, attr_int(node, "axis", 0), output)) {
                output = gather_tensor(inputs[0], inputs[1], attr_int(node, "axis", 0));
            }
        } else if (op == "GatherElements") {
            if (!device_gather_elements(slots, attr_int(node, "axis", 0), output)) {
                output = gather_elements_tensor(inputs[0], inputs[1], attr_int(node, "axis", 0));
            }
            layout_shape_note("gather_elements " + shape_text(inputs[0].shape) + " " +
                              shape_text(inputs[1].shape) + " axis=" +
                              std::to_string(attr_int(node, "axis", 0)));
        } else if (op == "ScatterElements") {
            const fsv::AttributeProto* reduction_attr = find_attr(node, "reduction");
            const std::string reduction = reduction_attr ? reduction_attr->s : "none";
            if (!device_scatter_elements(slots, attr_int(node, "axis", 0), reduction, output)) {
                output = scatter_elements_tensor(inputs[0], inputs[1], inputs[2],
                    attr_int(node, "axis", 0), reduction);
            }
            layout_shape_note("scatter_elements " + shape_text(inputs[0].shape) + " " +
                              shape_text(inputs[1].shape) + " axis=" +
                              std::to_string(attr_int(node, "axis", 0)));
        } else if (op == "Cast") {
            output = cast_tensor(inputs[0], static_cast<int32_t>(attr_int(node, "to")));
        } else if (op == "CastLike") {
            output = cast_tensor(inputs[0], inputs[1].dtype);
        } else if (op == "SequenceEmpty") {
            sequence_value.is_sequence = true;
            sequence_value.sequence_dtype = static_cast<int32_t>(attr_int(node, "dtype", 1));
        } else if (op == "SplitToSequence") {
            std::vector<int64_t> split;
            if (inputs.size() > 1 && inputs[1].dtype) split = ints_from_tensor(inputs[1]);
            else if (const fsv::AttributeProto* split_attr = find_attr(node, "split")) split = split_attr->ints;
            int64_t axis = attr_int(node, "axis", 0);
            if (axis < 0) axis += static_cast<int64_t>(inputs[0].shape.size());
            sequence_value.is_sequence = true;
            sequence_value.sequence = split_to_sequence_values(inputs[0], split, axis,
                attr_int(node, "keepdims", 1) != 0);
            sequence_value.sequence_dtype = inputs[0].dtype;
        } else if (op == "ConcatFromSequence") {
            output = concat_from_sequence_values(input_values[0].sequence,
                attr_int(node, "axis", 0), attr_int(node, "new_axis", 0) != 0,
                input_values[0].sequence_dtype);
        } else if (op == "SequenceInsert") {
            int64_t position = static_cast<int64_t>(input_values[0].sequence.size());
            if (node.inputs.size() > 2 && !node.inputs[2].empty()) {
                position = read_int_scalar(inputs[2]);
            }
            sequence_value = sequence_insert_value(input_values[0], inputs[1], position);
            if (!sequence_value.is_sequence) {
                error = "SequenceInsert failed: " + node.name;
                return false;
            }
        } else if (op == "SequenceAt") {
            output = sequence_at_tensor(input_values[0],
                read_int_scalar(inputs[1]));
        } else if (op == "PRelu") {
            output = prelu_tensor(inputs[0], inputs[1]);
        } else if (op == "Tile") {
            output = tile_tensor(inputs[0], ints_from_tensor(inputs[1]));
        } else if (op == "ArgMax") {
            const int64_t argmax_axis = attr_int(node, "axis", 0);
            const bool argmax_keepdims = attr_int(node, "keepdims", 1) != 0;
            if (!device_argmax(slots, argmax_axis, argmax_keepdims, output)) {
                output = argmax_tensor(inputs[0], argmax_axis, argmax_keepdims);
            }
            layout_shape_note("argmax " + shape_text(inputs[0].shape) + " axis=" +
                              std::to_string(argmax_axis) + " -> " +
                              shape_text(output.shape));
        } else if (op == "TopK") {
            multi_outputs = topk_tensors(inputs[0], read_int_scalar(inputs[1]),
                attr_int(node, "axis", -1), attr_int(node, "largest", 1) != 0,
                attr_int(node, "sorted", 1) != 0);
        } else if (op == "Split") {
            int64_t split_axis = attr_int(node, "axis", 0);
            if (split_axis < 0) split_axis += static_cast<int64_t>(inputs[0].shape.size());
            if (split_axis < 0 || split_axis >= static_cast<int64_t>(inputs[0].shape.size())) {
                error = "Split axis out of range";
                return false;
            }
            std::vector<int64_t> split;
            if (inputs.size() > 1 && inputs[1].dtype) split = ints_from_tensor(inputs[1]);
            else if (const fsv::AttributeProto* split_attr = find_attr(node, "split")) split = split_attr->ints;
            if (split.empty()) {
                int64_t count = static_cast<int64_t>(node.outputs.size());
                int64_t dim = inputs[0].shape[static_cast<std::size_t>(split_axis)];
                int64_t base = count ? dim / count : 0;
                int64_t remainder = count ? dim % count : 0;
                for (int64_t index = 0; index < count; ++index) {
                    split.push_back(base + (index < remainder ? 1 : 0));
                }
            }
            if (!device_split(slots, split, split_axis, multi_outputs)) {
                multi_outputs = split_tensors(inputs[0], split, split_axis);
            }
            {
                std::string note = "split axis=" + std::to_string(split_axis) + " " +
                                   shape_text(inputs[0].shape);
                for (int64_t value : split) note += " " + std::to_string(value);
                layout_shape_note(note);
            }
        } else if (op == "DFT") {
            const Tensor* dft_length = nullptr;
            if (node.inputs.size() > 1 && !node.inputs[1].empty() && inputs[1].dtype) {
                dft_length = &inputs[1];
            }
            output = dft_tensor(inputs[0], dft_length, attr_int(node, "axis", 1),
                                attr_int(node, "inverse", 0) != 0,
                                attr_int(node, "onesided", 0) != 0);
            if (!output.dtype) {
                error = "DFT failed: " + node.name;
                return false;
            }
        } else if (op == "STFT") {
            const Tensor* window = nullptr;
            const Tensor* frame_length = nullptr;
            if (inputs.size() > 2 && inputs[2].dtype) window = &inputs[2];
            if (inputs.size() > 3 && inputs[3].dtype) frame_length = &inputs[3];
            output = stft_tensor(inputs[0], inputs[1], window, frame_length,
                                 attr_int(node, "onesided", 1) != 0);
            if (!output.dtype) {
                error = "STFT failed: " + node.name;
                return false;
            }
        } else if (op == "MatMulNBits") {
            int64_t k = attr_int(node, "K");
            int64_t n = attr_int(node, "N");
            int64_t block_size = attr_int(node, "block_size");
            if (!device_matmul_nbits(slots, k, n, block_size, output)) {
                Tensor packed = initializers_.at(node.inputs[1]);
                Tensor scales = initializers_.at(node.inputs[2]);
                output = matmul_nbits_tensor(inputs[0], packed, scales, k, n, block_size);
            }
        } else if (op == "Conv") {
            const fsv::AttributeProto* pads_attr = find_attr(node, "pads");
            const fsv::AttributeProto* strides_attr = find_attr(node, "strides");
            const fsv::AttributeProto* dilations_attr = find_attr(node, "dilations");
            std::vector<int64_t> pads = pads_attr ? pads_attr->ints : std::vector<int64_t>{};
            std::vector<int64_t> strides = strides_attr ? strides_attr->ints
                                                        : std::vector<int64_t>{1};
            std::vector<int64_t> dilations = dilations_attr ? dilations_attr->ints
                                                            : std::vector<int64_t>{1};
            if (!device_conv(slots, pads, strides, dilations,
                             attr_int(node, "group", 1), output)) {
                Tensor bias;
                if (node.inputs.size() > 2 && !node.inputs[2].empty()) bias = inputs[2];
                const Tensor* conv_bias = node.inputs.size() > 2 &&
                    !node.inputs[2].empty() ? &bias : nullptr;
                if (inputs[0].shape.size() == 4) {
                    output = conv2d_tensor(inputs[0], inputs[1], conv_bias,
                        pads, strides, dilations, attr_int(node, "group", 1));
                } else {
                    output = conv1d_tensor(inputs[0], inputs[1], conv_bias,
                        pads, strides, dilations, attr_int(node, "group", 1));
                }
            }
        } else if (op == "ConvTranspose") {
            const fsv::AttributeProto* pads_attr = find_attr(node, "pads");
            const fsv::AttributeProto* strides_attr = find_attr(node, "strides");
            const fsv::AttributeProto* dilations_attr = find_attr(node, "dilations");
            const fsv::AttributeProto* output_padding_attr = find_attr(node, "output_padding");
            std::vector<int64_t> pads = pads_attr ? pads_attr->ints : std::vector<int64_t>{};
            std::vector<int64_t> strides = strides_attr ? strides_attr->ints
                                                        : std::vector<int64_t>{1};
            std::vector<int64_t> dilations = dilations_attr ? dilations_attr->ints
                                                            : std::vector<int64_t>{1};
            std::vector<int64_t> output_padding = output_padding_attr
                ? output_padding_attr->ints : std::vector<int64_t>{};
            if (!device_conv_transpose(slots, pads, strides, dilations, output_padding,
                                       attr_int(node, "group", 1), output)) {
                Tensor bias;
                if (node.inputs.size() > 2 && !node.inputs[2].empty()) bias = inputs[2];
                output = conv_transpose1d_tensor(inputs[0], inputs[1],
                    node.inputs.size() > 2 && !node.inputs[2].empty() ? &bias : nullptr,
                    pads, strides, dilations, output_padding, attr_int(node, "group", 1));
            }
        } else if (op == "Softmax") {
            if (!device_softmax(slots, attr_int(node, "axis", -1), output)) {
                output = softmax_tensor(inputs[0], attr_int(node, "axis", -1));
            }
        } else if (op == "LayerNormalization") {
            float epsilon = 1e-5f;
            if (const fsv::AttributeProto* eps_attr = find_attr(node, "epsilon")) epsilon = eps_attr->f;
            if (!device_layer_norm(slots, attr_int(node, "axis", -1), epsilon, output)) {
                Tensor zero_bias = make_tensor(inputs[1].shape, inputs[1].dtype);
                const Tensor& bias = inputs.size() > 2 && inputs[2].dtype ? inputs[2] : zero_bias;
                output = layer_normalization_tensor(inputs[0], inputs[1], bias,
                    attr_int(node, "axis", -1), epsilon);
            }
        } else if (op == "BatchNormalization") {
            Tensor empty;
            float epsilon = 1e-5f;
            if (const fsv::AttributeProto* eps_attr = find_attr(node, "epsilon")) epsilon = eps_attr->f;
            if (!device_batch_norm(slots, epsilon, output)) {
                output = batch_normalization_tensor(inputs[0], inputs[1], inputs[2],
                    inputs.size() > 3 && inputs[3].dtype ? inputs[3] : empty,
                    inputs.size() > 4 && inputs[4].dtype ? inputs[4] : empty, epsilon);
            }
        } else if (op == "Gemm") {
            float alpha = 1.0f;
            float beta = 1.0f;
            if (const fsv::AttributeProto* alpha_attr = find_attr(node, "alpha")) alpha = alpha_attr->f;
            if (const fsv::AttributeProto* beta_attr = find_attr(node, "beta")) beta = beta_attr->f;
            if (!device_gemm(slots, alpha, beta, attr_int(node, "transA", 0) != 0,
                             attr_int(node, "transB", 0) != 0, output)) {
                output = gemm_tensor(inputs[0], inputs[1],
                    inputs.size() > 2 && inputs[2].dtype ? &inputs[2] : nullptr,
                    alpha, beta, attr_int(node, "transA", 0) != 0,
                    attr_int(node, "transB", 0) != 0);
            }
        } else if (op == "InstanceNormalization") {
            float epsilon = 1e-5f;
            if (const fsv::AttributeProto* eps_attr = find_attr(node, "epsilon")) epsilon = eps_attr->f;
            if (!device_instance_norm(slots, epsilon, output)) {
                output = instance_normalization_tensor(inputs[0], inputs[1], inputs[2], epsilon);
            }
        } else if (op == "CumSum") {
            int64_t axis = read_int_scalar(inputs[1], 0);
            Tensor source = inputs[0];
            if (axis < 0) axis += static_cast<int64_t>(source.shape.size());
            if (axis < 0 || axis >= static_cast<int64_t>(source.shape.size())) {
                error = "CumSum axis out of range";
                return false;
            }
            bool exclusive = attr_int(node, "exclusive", 0) != 0;
            bool reverse = attr_int(node, "reverse", 0) != 0;
            if (source.dtype == 1) output = cumsum_tensor<float>(source, axis, exclusive, reverse);
            else if (source.dtype == 6) output = cumsum_tensor<std::int32_t>(source, axis, exclusive, reverse);
            else if (source.dtype == 7) output = cumsum_tensor<std::int64_t>(source, axis, exclusive, reverse);
            else {
                error = "unsupported CumSum dtype";
                return false;
            }
        } else if (op == "If") {
            const fsv::AttributeProto* then_attr = find_attr(node, "then_branch");
            const fsv::AttributeProto* else_attr = find_attr(node, "else_branch");
            if (!then_attr || !else_attr || !then_attr->graph || !else_attr->graph) {
                error = "If branch graph missing: " + node.name;
                return false;
            }
            const fsv::GraphProto* branch = then_attr->graph.get();
            if (input_values.empty() || !input_values[0].tensor ||
                read_numeric(*input_values[0].tensor, 0) == 0.0) {
                branch = else_attr->graph.get();
            }
            std::unordered_map<std::string, Value> branch_values = values;
            Scope branch_scope;
            branch_scope.map = &branch_values;
            if (!execute(*branch, branch_scope, error, false)) return false;
            flow_values.clear();
            for (const ValueInfo& info : branch->outputs) {
                auto iterator = branch_values.find(info.name);
                if (iterator == branch_values.end()) {
                    error = "If branch output missing: " + info.name;
                    return false;
                }
                values[info.name] = iterator->second;
                flow_values.push_back(iterator->second);
            }
        } else if (op == "Loop") {
            const fsv::AttributeProto* body_attr = find_attr(node, "body");
            if (!body_attr || !body_attr->graph) {
                error = "Loop body graph missing: " + node.name;
                return false;
            }
            const fsv::GraphProto& body = *body_attr->graph;
            int64_t max_trip = std::numeric_limits<int64_t>::max();
            if (input_values.size() > 0 && input_values[0].tensor) {
                max_trip = read_int_scalar(*input_values[0].tensor);
            }
            bool condition = true;
            if (input_values.size() > 1 && input_values[1].tensor) {
                condition = read_numeric(*input_values[1].tensor, 0) != 0.0;
            }
            std::vector<Value> carried;
            for (std::size_t index = 2; index < input_values.size(); ++index) {
                carried.push_back(input_values[index]);
            }
            if (body.inputs.size() < 2 || body.inputs.size() - 2 != carried.size()) {
                error = "Loop body input count mismatch: " + node.name;
                return false;
            }
            for (int64_t iteration = 0; iteration < max_trip && condition; ++iteration) {
                std::unordered_map<std::string, Value> body_values = values;
                Tensor iteration_tensor = make_tensor({}, 7);
                write_scalar<int64_t>(iteration_tensor, 0, iteration);
                Tensor condition_tensor = make_tensor({}, 9);
                write_scalar<std::uint8_t>(condition_tensor, 0, condition ? 1 : 0);
                Value iteration_value;
                iteration_value.tensor = std::make_shared<Tensor>(std::move(iteration_tensor));
                Value condition_value;
                condition_value.tensor = std::make_shared<Tensor>(std::move(condition_tensor));
                body_values[body.inputs[0].name] = iteration_value;
                body_values[body.inputs[1].name] = condition_value;
                for (std::size_t index = 0; index < carried.size(); ++index) {
                    body_values[body.inputs[index + 2].name] = carried[index];
                }
                Scope body_scope;
                body_scope.map = &body_values;
                if (!execute(body, body_scope, error, false)) return false;
                if (body.outputs.empty()) {
                    error = "Loop body output missing: " + node.name;
                    return false;
                }
                auto condition_iterator = body_values.find(body.outputs[0].name);
                if (condition_iterator == body_values.end() ||
                    !condition_iterator->second.tensor) {
                    error = "Loop body condition output missing: " + node.name;
                    return false;
                }
                condition = read_numeric(*condition_iterator->second.tensor, 0) != 0.0;
                for (std::size_t index = 0; index < carried.size(); ++index) {
                    std::size_t output_index = index + 1;
                    if (output_index >= body.outputs.size()) {
                        error = "Loop body output count mismatch: " + node.name;
                        return false;
                    }
                    auto iterator = body_values.find(body.outputs[output_index].name);
                    if (iterator == body_values.end()) {
                        error = "Loop body carried output missing: " + node.name;
                        return false;
                    }
                    carried[index] = iterator->second;
                }
            }
            if (carried.size() != node.outputs.size()) {
                error = "Loop output count mismatch: " + node.name;
                return false;
            }
            for (std::size_t index = 0; index < carried.size(); ++index) {
                if (!node.outputs[index].empty()) values[node.outputs[index]] = carried[index];
            }
            flow_values = std::move(carried);
        } else {
            error = "native runtime does not support op yet: " + op + " (" + node.name + ")";
            return false;
        }
        const std::chrono::steady_clock::time_point probe_executed = probe
            ? std::chrono::steady_clock::now()
            : std::chrono::steady_clock::time_point();
        if (!flow_values.empty()) {
            if (flow_values.size() != node.outputs.size()) {
                error = "native op output count mismatch for " + op + " (" + node.name + ")";
                return false;
            }
            for (std::size_t index = 0; index < flow_values.size(); ++index) {
                if (Value* slot = output_slot(index)) *slot = std::move(flow_values[index]);
            }
        } else if (sequence_value.is_sequence) {
            if (trace) {
                FSV_TRACE( "[native-run]   out sequence size=%zu\n",
                             sequence_value.sequence.size());
            }
            for (std::size_t index = 0; index < node.outputs.size(); ++index) {
                if (Value* slot = output_slot(index)) *slot = sequence_value;
            }
        } else if (!multi_outputs.empty()) {
            if (multi_outputs.size() != node.outputs.size()) {
                error = "native op output count mismatch for " + op + " (" + node.name + ")";
                return false;
            }
            for (std::size_t index = 0; index < multi_outputs.size(); ++index) {
                if (trace) {
                    FSV_TRACE( "[native-run]   out%zu shape=%s dtype=%d\n", index,
                        shape_text(multi_outputs[index].shape).c_str(), multi_outputs[index].dtype);
                }
                Value value;
                value.tensor = std::make_shared<Tensor>(std::move(multi_outputs[index]));
                if (Value* slot = output_slot(index)) *slot = value;
            }
        } else {
            if (output.dtype == 0) {
                error = "native op failed for " + op + " (" + node.name + ")";
                return false;
            }
            if (trace) {
                FSV_TRACE( "[native-run]   out shape=%s dtype=%d\n",
                             shape_text(output.shape).c_str(), output.dtype);
            }
            Value value;
            value.tensor = std::make_shared<Tensor>(std::move(output));
            for (std::size_t index = 0; index < node.outputs.size(); ++index) {
                if (Value* slot = output_slot(index)) *slot = value;
            }
        }
        if (release_unused) {
            if (planned) {
                /* The read counts were walked once, in prepare(); the run only
                   counts them down. */
                const std::size_t base = (*plan_input_offset)[node_index];
                std::vector<std::size_t>& counts = *scope.remaining;
                for (std::size_t index = 0; index < input_count; ++index) {
                    const std::size_t slot = (*plan_inputs)[base + index];
                    if (slot == kNoValueSlot) continue;
                    if (--counts[slot] != 0) continue;
                    Value& value = *scope.plan->slots[slot];
                    if (value.tensor) release_device_copy(*value.tensor);
                }
            } else {
            for (const std::string& name : node.inputs) {
                if (name.empty()) continue;
                auto remaining = remaining_uses.find(name);
                if (remaining == remaining_uses.end()) continue;
                if (--remaining->second) continue;
                remaining_uses.erase(remaining);
                auto value = values.find(name);
                if (value != values.end() && value->second.tensor) {
                    release_device_copy(*value->second.tensor);
                }
            }
            }
        }
        if (probe) {
            NodeProbe& counters = node_probe();
            ++counters.nodes;
            counters.gather +=
                std::chrono::duration<double>(probe_gathered - probe_start).count();
            counters.exec +=
                std::chrono::duration<double>(probe_executed - probe_gathered).count();
            counters.store += std::chrono::duration<double>(
                std::chrono::steady_clock::now() - probe_executed).count();
        }
    }
        return true;
    };
    Scope root_scope;
    root_scope.plan = plan_.get();
    root_scope.remaining = &remaining;
    if (!execute(model_.graph, root_scope, error, true)) return false;
    const char* capture_env = capture_ ? "requested" : std::getenv("FSV_NATIVE_CAPTURE");
    FSV_TRACE( "[native-run] capture=%s values=%zu\n",
                 capture_env ? capture_env : "(null)", values_.size());
    if (capture_env) {
        captured_.clear();
        std::size_t shown = 0;
        for (const auto& item : values_) {
            if (item.second.tensor) {
                captured_[item.first] = *item.second.tensor;
                if (shown < 40) {
                    FSV_TRACE( "[native-run] captured[%zu]=%s\n", shown, item.first.c_str());
                    ++shown;
                }
            }
        }
    }
    outputs.clear();
    for (const ValueInfo& info : model_.graph.outputs) {
        fsv::opstats::Scope output_scope("(graph-output)");
        auto iterator = values_.find(info.name);
        if (iterator == values_.end() || !iterator->second.tensor) {
            error = "graph output missing: " + info.name;
            return false;
        }
        if (resident_output(info.name)) {
            resident_[info.name] = *iterator->second.tensor;
            const Tensor& produced = *iterator->second.tensor;
            Tensor placeholder;
            placeholder.shape = produced.shape;
            placeholder.dtype = produced.dtype;
            outputs[info.name] = std::move(placeholder);
            continue;
        }
        ensure_host(*iterator->second.tensor);
        outputs[info.name] = *iterator->second.tensor;
    }
    return true;
}

}  // namespace fsv
