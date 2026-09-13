#include "fsv_native_graph.h"

#include "fsv_graph_runtime.h"

#include <cstdlib>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

thread_local std::string g_graph_error;

/* Temporary diagnostic (FSV_NATIVE_RUN_TIME): how much of a synthesis is spent
   inside fsv_graph_run and how much the Python driver spends around it. */
struct RunClock {
    double seconds = 0.0;
    unsigned long long calls = 0;
    ~RunClock() {
        if (!std::getenv("FSV_NATIVE_RUN_TIME")) return;
        std::fprintf(stderr, "[run-time] %llu calls %.3fs\n", calls, seconds);
    }
} g_run_clock;

bool graph_trace_enabled() {
    static const bool enabled = std::getenv("FSV_NATIVE_TRACE") != nullptr;
    return enabled;
}

std::size_t graph_element_size(int32_t dtype) {
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

std::string parent_directory(const char* path) {
    if (!path) return {};
    std::string value(path);
    std::size_t position = value.find_last_of("\\/");
    return position == std::string::npos ? std::string{} : value.substr(0, position);
}

bool copy_graph_tensor(const std::string& name, const fsv::Tensor& tensor,
                       fsv_graph_output& output) {
    output.name = name.c_str();
    output.dtype = tensor.dtype;
    output.ndim = static_cast<int32_t>(tensor.shape.size());
    output.dims = static_cast<int64_t*>(std::malloc(
        sizeof(int64_t) * (tensor.shape.empty() ? 1 : tensor.shape.size())));
    if (!output.dims) return false;
    for (std::size_t dim = 0; dim < tensor.shape.size(); ++dim) {
        output.dims[dim] = tensor.shape[dim];
    }
    output.data = std::malloc(tensor.data.size() ? tensor.data.size() : 1);
    if (!output.data) {
        std::free(output.dims);
        output.dims = nullptr;
        return false;
    }
    if (!tensor.data.empty()) {
        std::memcpy(output.data, tensor.data.data(), tensor.data.size());
    }
    return true;
}

/* A device buffer on loan from another graph. The shared handle keeps the
   bytes alive until the borrowing run has bound them, and the shape and type
   travel with it because a borrowed input arrives with no host payload to read
   them from. */
struct DeviceLoan {
    std::shared_ptr<fsv::TensorDeviceStorage> storage;
    std::vector<int64_t> shape;
    int32_t dtype = 0;
};

}  // namespace

struct fsv_graph_handle {
    explicit fsv_graph_handle(fsv::ModelProto model, std::string dir, std::string weights)
        : runtime(std::move(model), std::move(dir), std::move(weights)) {}
    fsv::GraphRuntime runtime;
    std::vector<std::string> input_names;
    std::vector<std::string> output_names;
    std::vector<std::string> captured_names;
};

extern "C" {

struct fsv_graph_handle* fsv_graph_create(const char* model_path,
                                          const char* external_weights) {
    g_graph_error.clear();
    try {
        fsv::ModelProto model;
        std::string error;
        if (!fsv::load_onnx_model(model_path, model, error)) {
            g_graph_error = error;
            return nullptr;
        }
        std::unique_ptr<fsv_graph_handle> handle(new fsv_graph_handle(
            std::move(model), parent_directory(model_path),
            external_weights ? external_weights : std::string{}));
        if (!handle->runtime.prepare(error)) {
            g_graph_error = error;
            return nullptr;
        }
        handle->output_names = handle->runtime.output_names();
        handle->input_names = handle->runtime.input_names();
        return handle.release();
    } catch (const std::exception& exc) {
        g_graph_error = exc.what();
        return nullptr;
    }
}

int fsv_graph_run(struct fsv_graph_handle* handle,
                  const fsv_graph_tensor* inputs, std::size_t input_count,
                  fsv_graph_output* outputs, std::size_t output_count) {
    if (!handle || !outputs || (!inputs && input_count != 0)) {
        g_graph_error = "invalid graph run arguments";
        return 1;
    }
    try {
        struct RunTimer {
            std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
            ~RunTimer() {
                g_run_clock.seconds += std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - start).count();
                ++g_run_clock.calls;
            }
        } run_timer;
        if (graph_trace_enabled()) {
            std::fprintf(stderr, "[fsv-graph] run begin inputs=%zu outputs=%zu\n", input_count, output_count);
        }
        std::unordered_map<std::string, fsv::Tensor> feeds;
        for (std::size_t index = 0; index < input_count; ++index) {
            fsv::Tensor tensor;
            tensor.dtype = inputs[index].dtype;
            tensor.shape.assign(inputs[index].dims, inputs[index].dims + inputs[index].ndim);
            std::size_t count = 1;
            for (int32_t dim = 0; dim < inputs[index].ndim; ++dim) count *= static_cast<std::size_t>(tensor.shape[dim]);
            std::size_t bytes = count * graph_element_size(tensor.dtype);
            tensor.data.resize(bytes);
            if (bytes) std::memcpy(tensor.data.data(), inputs[index].data, bytes);
            feeds[inputs[index].name ? inputs[index].name : ""] = std::move(tensor);
        }
        if (graph_trace_enabled()) std::fprintf(stderr, "[fsv-graph] feeds copied\n");
        std::unordered_map<std::string, fsv::Tensor> outputs_map;
        std::string error;
        if (!handle->runtime.run(feeds, outputs_map, error)) {
            g_graph_error = error;
            return 1;
        }
        handle->captured_names = handle->runtime.captured_names();
        if (graph_trace_enabled()) std::fprintf(stderr, "[fsv-graph] runtime run ok\n");
        if (output_count != handle->output_names.size()) {
            g_graph_error = "graph output count mismatch";
            return 1;
        }
        std::size_t index = 0;
        for (const std::string& name : handle->output_names) {
            auto iterator = outputs_map.find(name);
            if (iterator == outputs_map.end()) {
                g_graph_error = "graph output missing: " + name;
                return 1;
            }
            const fsv::Tensor& tensor = iterator->second;
            outputs[index].name = name.c_str();
            outputs[index].dtype = tensor.dtype;
            outputs[index].ndim = static_cast<int32_t>(tensor.shape.size());
            outputs[index].dims = static_cast<int64_t*>(std::malloc(
                sizeof(int64_t) * (tensor.shape.empty() ? 1 : tensor.shape.size())));
            if (!outputs[index].dims) {
                g_graph_error = "output dims allocation failed";
                return 1;
            }
            for (std::size_t dim = 0; dim < tensor.shape.size(); ++dim) outputs[index].dims[dim] = tensor.shape[dim];
            if (handle->runtime.resident_output(name)) {
                /* The value is the device buffer. Handing back an empty payload
                   is what tells the caller to keep passing it as a handle. */
                outputs[index].data = nullptr;
                ++index;
                continue;
            }
            outputs[index].data = std::malloc(tensor.data.size() ? tensor.data.size() : 1);
            if (!outputs[index].data) {
                std::free(outputs[index].dims);
                g_graph_error = "output data allocation failed";
                return 1;
            }
            if (!tensor.data.empty()) std::memcpy(outputs[index].data, tensor.data.data(), tensor.data.size());
            ++index;
        }
        return 0;
    } catch (const std::exception& exc) {
        g_graph_error = exc.what();
        return 1;
    }
}

void fsv_graph_free(struct fsv_graph_handle* handle) {
    delete handle;
}

int fsv_graph_input_count(struct fsv_graph_handle* handle) {
    return handle ? static_cast<int>(handle->input_names.size()) : 0;
}

const char* fsv_graph_input_name(struct fsv_graph_handle* handle, int index) {
    if (!handle || index < 0 || index >= static_cast<int>(handle->input_names.size())) return nullptr;
    return handle->input_names[static_cast<std::size_t>(index)].c_str();
}

int fsv_graph_output_count(struct fsv_graph_handle* handle) {
    return handle ? static_cast<int>(handle->output_names.size()) : 0;
}

const char* fsv_graph_output_name(struct fsv_graph_handle* handle, int index) {
    if (!handle || index < 0 || index >= static_cast<int>(handle->output_names.size())) return nullptr;
    return handle->output_names[static_cast<std::size_t>(index)].c_str();
}

int fsv_graph_captured_count(struct fsv_graph_handle* handle) {
    return handle ? static_cast<int>(handle->captured_names.size()) : 0;
}

const char* fsv_graph_captured_name(struct fsv_graph_handle* handle, int index) {
    if (!handle || index < 0 || index >= static_cast<int>(handle->captured_names.size())) return nullptr;
    return handle->captured_names[static_cast<std::size_t>(index)].c_str();
}

void fsv_graph_set_capture(struct fsv_graph_handle* handle, int enabled) {
    if (handle) handle->runtime.set_capture(enabled != 0);
}

/* Declares the {output, input} pairs whose value stays on the card between
   runs. Names are flat: out0, in0, out1, in1, ... */
void fsv_graph_set_resident(struct fsv_graph_handle* handle,
                            const char* const* names, int pair_count) {
    if (!handle || !names || pair_count <= 0) return;
    std::vector<std::pair<std::string, std::string>> pairs;
    pairs.reserve(static_cast<std::size_t>(pair_count));
    for (int index = 0; index < pair_count; ++index) {
        const char* output = names[index * 2];
        const char* input = names[index * 2 + 1];
        if (!output || !input) continue;
        pairs.emplace_back(output, input);
    }
    handle->runtime.set_resident(pairs);
}

/* Host bytes of a retained output, for a caller that really wants to read it. */
int fsv_graph_read_resident(struct fsv_graph_handle* handle, const char* name,
                            fsv_graph_output* output) {
    if (!handle || !name || !output) {
        g_graph_error = "invalid resident tensor arguments";
        return 1;
    }
    try {
        const fsv::Tensor* tensor = handle->runtime.find_resident(name);
        if (!tensor) {
            g_graph_error = std::string("resident tensor missing: ") + name;
            return 1;
        }
        if (!copy_graph_tensor(name, *tensor, *output)) {
            g_graph_error = "resident tensor allocation failed";
            return 1;
        }
        return 0;
    } catch (const std::exception& exc) {
        g_graph_error = exc.what();
        return 1;
    }
}

/* Declares outputs that stay on the card between runs without a paired input:
   a run reports such an output with a null ``data``, and the caller hands the
   buffer to another graph instead of reading its bytes. */
void fsv_graph_set_retained(struct fsv_graph_handle* handle,
                            const char* const* names, int count) {
    if (!handle || !names || count <= 0) return;
    std::vector<std::string> retained;
    retained.reserve(static_cast<std::size_t>(count));
    for (int index = 0; index < count; ++index) {
        if (names[index]) retained.emplace_back(names[index]);
    }
    handle->runtime.set_retained(retained);
}

/* Lends one retained output's device buffer to another graph. */
void* fsv_graph_borrow_device(struct fsv_graph_handle* handle, const char* name) {
    if (!handle || !name) return nullptr;
    try {
        std::unique_ptr<DeviceLoan> loan(new DeviceLoan());
        loan->storage = handle->runtime.borrow_device(name, loan->shape, loan->dtype);
        if (!loan->storage) return nullptr;
        return loan.release();
    } catch (const std::exception& exc) {
        g_graph_error = exc.what();
        return nullptr;
    }
}

void fsv_graph_release_device(void* borrowed) {
    delete static_cast<DeviceLoan*>(borrowed);
}

/* Binds a borrowed device buffer to the name a run reads as an input, so the
   next run binds the value without an upload. */
int fsv_graph_import_device(struct fsv_graph_handle* handle, const char* name,
                            const void* borrowed) {
    if (!handle || !name || !borrowed) {
        g_graph_error = "invalid borrowed tensor arguments";
        return 1;
    }
    const DeviceLoan* loan = static_cast<const DeviceLoan*>(borrowed);
    fsv::Tensor tensor;
    tensor.shape = loan->shape;
    tensor.dtype = loan->dtype;
    tensor.device = loan->storage;
    handle->runtime.set_device_feed(name, std::move(tensor));
    return 0;
}

void fsv_graph_release_outputs(fsv_graph_output* outputs, std::size_t count) {
    for (std::size_t index = 0; index < count; ++index) {
        std::free(outputs[index].data);
        std::free(outputs[index].dims);
        outputs[index].data = nullptr;
        outputs[index].dims = nullptr;
    }
}

int fsv_graph_get_tensor(struct fsv_graph_handle* handle, const char* name,
                         fsv_graph_output* output) {
    if (!handle || !name || !output) {
        g_graph_error = "invalid graph tensor arguments";
        return 1;
    }
    try {
        const fsv::Tensor* tensor = handle->runtime.find_captured(name);
        if (!tensor) {
            g_graph_error = std::string("captured tensor missing: ") + name;
            return 1;
        }
        if (!copy_graph_tensor(name, *tensor, *output)) {
            g_graph_error = "captured tensor allocation failed";
            return 1;
        }
        return 0;
    } catch (const std::exception& exc) {
        g_graph_error = exc.what();
        return 1;
    }
}

const char* fsv_graph_last_error(void) {
    return g_graph_error.c_str();
}

}  // extern "C"
