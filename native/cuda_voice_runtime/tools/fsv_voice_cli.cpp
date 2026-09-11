/* Voice package runner for the self-written CUDA runtime.

   Two modes:
     replay - run every graph with the fixture's inputs and report the error
              against the ONNX Runtime reference stored in the same fixture
     synth  - run the full acoustic pipeline on the GPU and write a WAV

   The fixture comes from tools/dev_dump_fixture.py and is a development aid;
   the runtime itself never reads it.

   Usage:
     fsv_voice_cli --package <voice package> --fixture <fixture.fsvt> \
                   --mode replay|synth --wav out.wav [--max-steps N]
*/

#include "fsv_native_graph.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

struct Tensor {
    int32_t dtype = 0;
    std::vector<int64_t> shape;
    std::vector<uint8_t> data;

    int64_t elements() const {
        int64_t total = 1;
        for (int64_t dim : shape) total *= dim > 0 ? dim : 1;
        return shape.empty() ? 1 : total;
    }
};

using TensorMap = std::map<std::string, Tensor>;

int dtype_size(int32_t dtype) {
    switch (dtype) {
        case 1: case 6: case 12: return 4;
        case 2: case 3: case 9: return 1;
        case 4: case 5: case 10: return 2;
        case 7: case 11: case 13: return 8;
        default: return 0;
    }
}

const std::string* tensor_name(const TensorMap& tensors, const std::string& name) {
    auto iterator = tensors.find(name);
    return iterator == tensors.end() ? nullptr : &iterator->first;
}

bool load_fixture(const char* path, TensorMap& out, std::string& error) {
    std::FILE* file = std::fopen(path, "rb");
    if (!file) {
        error = std::string("cannot open fixture: ") + path;
        return false;
    }
    char magic[9] = {};
    if (std::fread(magic, 1, 8, file) != 8 || std::strcmp(magic, "FSVTFIX1") != 0) {
        error = "fixture has an unexpected header";
        std::fclose(file);
        return false;
    }
    uint32_t count = 0;
    if (std::fread(&count, sizeof(count), 1, file) != 1) {
        error = "fixture header is truncated";
        std::fclose(file);
        return false;
    }
    for (uint32_t index = 0; index < count; ++index) {
        uint32_t name_length = 0;
        if (std::fread(&name_length, sizeof(name_length), 1, file) != 1) { error = "fixture is truncated"; std::fclose(file); return false; }
        std::string name(name_length, '\0');
        if (name_length && std::fread(&name[0], 1, name_length, file) != name_length) { error = "fixture is truncated"; std::fclose(file); return false; }
        Tensor tensor;
        int32_t dimensions = 0;
        if (std::fread(&tensor.dtype, sizeof(tensor.dtype), 1, file) != 1 ||
            std::fread(&dimensions, sizeof(dimensions), 1, file) != 1) { error = "fixture is truncated"; std::fclose(file); return false; }
        tensor.shape.resize(dimensions > 0 ? dimensions : 0);
        for (int32_t dim = 0; dim < dimensions; ++dim) {
            int64_t value = 0;
            if (std::fread(&value, sizeof(value), 1, file) != 1) { error = "fixture is truncated"; std::fclose(file); return false; }
            tensor.shape[static_cast<std::size_t>(dim)] = value;
        }
        uint64_t bytes = 0;
        if (std::fread(&bytes, sizeof(bytes), 1, file) != 1) { error = "fixture is truncated"; std::fclose(file); return false; }
        tensor.data.resize(static_cast<std::size_t>(bytes));
        if (bytes && std::fread(tensor.data.data(), 1, static_cast<std::size_t>(bytes), file) != bytes) {
            error = "fixture is truncated";
            std::fclose(file);
            return false;
        }
        out[name] = std::move(tensor);
    }
    std::fclose(file);
    return true;
}

struct Graph {
    fsv_graph_handle* handle = nullptr;
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;

    ~Graph() { if (handle) fsv_graph_free(handle); }

    bool open(const std::string& model, const std::string& external, std::string& error) {
        handle = fsv_graph_create(model.c_str(), external.empty() ? nullptr : external.c_str());
        if (!handle) {
            error = fsv_graph_last_error();
            return false;
        }
        if (std::getenv("FSV_NATIVE_CAPTURE")) fsv_graph_set_capture(handle, 1);
        int input_count = fsv_graph_input_count(handle);
        int output_count = fsv_graph_output_count(handle);
        for (int index = 0; index < input_count; ++index) inputs.emplace_back(fsv_graph_input_name(handle, index));
        for (int index = 0; index < output_count; ++index) outputs.emplace_back(fsv_graph_output_name(handle, index));
        return true;
    }

    int index_of(const std::string& name) const {
        auto iterator = std::find(inputs.begin(), inputs.end(), name);
        return iterator == inputs.end() ? -1 : static_cast<int>(iterator - inputs.begin());
    }

    bool run(const std::vector<const Tensor*>& feeds, std::vector<Tensor>& results, std::string& error) {
        if (feeds.size() != inputs.size()) {
            error = "feed count does not match the graph inputs";
            return false;
        }
        std::vector<fsv_graph_tensor> input_array(feeds.size());
        for (std::size_t index = 0; index < feeds.size(); ++index) {
            input_array[index].name = inputs[index].c_str();
            input_array[index].data = feeds[index]->data.data();
            input_array[index].dtype = feeds[index]->dtype;
            input_array[index].dims = feeds[index]->shape.data();
            input_array[index].ndim = static_cast<int32_t>(feeds[index]->shape.size());
        }
        std::vector<fsv_graph_output> output_array(outputs.size());
        int status = fsv_graph_run(handle, input_array.data(), input_array.size(),
                                   output_array.data(), output_array.size());
        if (status != 0) {
            error = fsv_graph_last_error();
            return false;
        }
        results.clear();
        for (std::size_t index = 0; index < outputs.size(); ++index) {
            Tensor tensor;
            tensor.dtype = output_array[index].dtype;
            tensor.shape.assign(output_array[index].dims,
                                output_array[index].dims + output_array[index].ndim);
            std::size_t bytes = static_cast<std::size_t>(tensor.elements()) * dtype_size(tensor.dtype);
            tensor.data.assign(static_cast<uint8_t*>(output_array[index].data),
                               static_cast<uint8_t*>(output_array[index].data) + bytes);
            results.push_back(std::move(tensor));
        }
        fsv_graph_release_outputs(output_array.data(), output_array.size());
        return true;
    }
};

const float* as_float(const Tensor& tensor) { return reinterpret_cast<const float*>(tensor.data.data()); }
const int64_t* as_int64(const Tensor& tensor) { return reinterpret_cast<const int64_t*>(tensor.data.data()); }

bool is_float_dtype(int32_t dtype) { return dtype == 1 || dtype == 10; }

double read_float(const Tensor& tensor, int64_t index) {
    if (tensor.dtype == 10) {
        uint16_t bits = reinterpret_cast<const uint16_t*>(tensor.data.data())[index];
        uint32_t sign = static_cast<uint32_t>(bits & 0x8000) << 16;
        uint32_t exponent = (bits >> 10) & 0x1F;
        uint32_t mantissa = bits & 0x3FF;
        uint32_t widened;
        if (exponent == 0) {
            if (mantissa == 0) {
                widened = sign;
            } else {
                int shift = 0;
                while ((mantissa & 0x400) == 0) { mantissa <<= 1; ++shift; }
                mantissa &= 0x3FF;
                widened = sign | ((113 - shift) << 23) | (mantissa << 13);
            }
        } else if (exponent == 0x1F) {
            widened = sign | 0x7F800000u | (mantissa << 13);
        } else {
            widened = sign | ((exponent + 112) << 23) | (mantissa << 13);
        }
        float value;
        std::memcpy(&value, &widened, sizeof(value));
        return value;
    }
    return as_float(tensor)[index];
}

/* Half precision tolerates a couple of ULPs of accumulation-order noise; the
   full precision graphs are held to a much tighter bound. */
double error_tolerance(int32_t dtype) { return dtype == 10 ? 5e-2 : 2e-3; }

std::string shape_text(const Tensor& tensor) {
    std::string text = "[";
    for (std::size_t index = 0; index < tensor.shape.size(); ++index) {
        if (index) text += ",";
        text += std::to_string(tensor.shape[index]);
    }
    return text + "]";
}

double max_abs_error(const Tensor& left, const Tensor& right) {
    if (left.shape != right.shape) return -1.0;
    if (!is_float_dtype(left.dtype) || !is_float_dtype(right.dtype)) return -1.0;
    double worst = 0.0;
    for (int64_t index = 0; index < left.elements(); ++index) {
        worst = std::fmax(worst, std::fabs(read_float(left, index) - read_float(right, index)));
    }
    return worst;
}

bool exact_equal(const Tensor& left, const Tensor& right) {
    return left.shape == right.shape && left.dtype == right.dtype && left.data == right.data;
}

struct Timer {
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    double seconds() const {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    }
};

/* Fixture inputs are matched by name, never by position. */
void collect_inputs(const TensorMap& fixture, const std::string& tag,
                    std::map<std::string, Tensor>& out) {
    const std::string prefix = tag + ".in.";
    for (const auto& item : fixture) {
        if (item.first.rfind(prefix, 0) != 0) continue;
        std::string rest = item.first.substr(prefix.size());
        std::size_t dot = rest.find('.');
        if (dot == std::string::npos) continue;
        out[rest.substr(dot + 1)] = item.second;
    }
}

bool order_feeds(const Graph& graph, const std::map<std::string, Tensor>& named,
                 std::vector<const Tensor*>& feeds, std::string& error) {
    feeds.assign(graph.inputs.size(), nullptr);
    for (std::size_t index = 0; index < graph.inputs.size(); ++index) {
        auto iterator = named.find(graph.inputs[index]);
        if (iterator == named.end()) {
            error = "fixture is missing input " + graph.inputs[index];
            return false;
        }
        feeds[index] = &iterator->second;
    }
    return true;
}

/* Writes every tensor the last run captured, in the fixture format, so the
   per-node comparison against ONNX Runtime can be done from Python. */
void write_captured(Graph& graph, const std::string& path) {
    std::FILE* file = std::fopen(path.c_str(), "wb");
    if (!file) {
        std::printf("cannot write capture: %s\n", path.c_str());
        return;
    }
    const int count = fsv_graph_captured_count(graph.handle);
    std::fwrite("FSVTFIX1", 1, 8, file);
    std::fwrite(&count, sizeof(count), 1, file);
    uint64_t written = 0;
    for (int index = 0; index < count; ++index) {
        const char* name = fsv_graph_captured_name(graph.handle, index);
        fsv_graph_output output{};
        if (!name || fsv_graph_get_tensor(graph.handle, name, &output) != 0) continue;
        const uint32_t name_length = static_cast<uint32_t>(std::strlen(name));
        std::fwrite(&name_length, sizeof(name_length), 1, file);
        std::fwrite(name, 1, name_length, file);
        const int32_t dimensions = output.ndim;
        std::fwrite(&output.dtype, sizeof(output.dtype), 1, file);
        std::fwrite(&dimensions, sizeof(dimensions), 1, file);
        int64_t elements = 1;
        for (int32_t dim = 0; dim < output.ndim; ++dim) {
            std::fwrite(&output.dims[dim], sizeof(int64_t), 1, file);
            elements *= output.dims[dim] > 0 ? output.dims[dim] : 1;
        }
        if (output.ndim == 0) elements = 1;
        const uint64_t bytes = static_cast<uint64_t>(elements) * dtype_size(output.dtype);
        std::fwrite(&bytes, sizeof(bytes), 1, file);
        std::fwrite(output.data, 1, static_cast<std::size_t>(bytes), file);
        ++written;
        fsv_graph_release_outputs(&output, 1);
    }
    std::fclose(file);
    std::printf("wrote %s (%llu tensors)\n", path.c_str(),
                static_cast<unsigned long long>(written));
}

int run_replay(const std::string& package, const TensorMap& fixture, const std::vector<std::pair<std::string, std::string>>& graphs, int max_iterations, const std::string& dump_path) {
    int failures = 0;
    for (const auto& entry : graphs) {
        const std::string& tag = entry.first;
        std::string model = package + entry.second;
        std::printf("--- %s: loading %s\n", tag.c_str(), model.c_str());
        std::fflush(stdout);
        std::string external = model.substr(0, model.size() - 5) + "_fp16.bin";
        if (tag == "first" || tag == "stage") external = package + "/character/t2s_shared_fp16.bin";
        std::FILE* probe = std::fopen(external.c_str(), "rb");
        if (probe) {
            std::fclose(probe);
        } else {
            external.clear();
        }

        Graph graph;
        std::string error;
        Timer load_timer;
        if (!graph.open(model, external, error)) {
            std::printf("%-10s OPEN FAILED  %s\n", tag.c_str(), error.c_str());
            ++failures;
            continue;
        }
        std::printf("           loaded in %.2fs (%zu inputs, %zu outputs)\n", load_timer.seconds(),
                    graph.inputs.size(), graph.outputs.size());
        std::map<std::string, Tensor> named;
        collect_inputs(fixture, tag, named);
        std::vector<const Tensor*> feeds;
        if (!order_feeds(graph, named, feeds, error)) {
            std::printf("%-10s SKIP  %s\n", tag.c_str(), error.c_str());
            ++failures;
            continue;
        }
        int iterations = (tag == "stage") ? max_iterations : 1;
        for (int iteration = 0; iteration < iterations; ++iteration) {
            std::vector<Tensor> results;
            Timer timer;
            if (!graph.run(feeds, results, error)) {
                std::printf("%-10s RUN FAILED  %s\n", tag.c_str(), error.c_str());
                ++failures;
                break;
            }
            double seconds = timer.seconds();
            double worst = 0.0;
            std::string worst_name = "-";
            int mismatches = 0;
            for (std::size_t index = 0; index < results.size(); ++index) {
                std::string key = tag + ".out." + std::to_string(index);
                auto reference = fixture.find(key);
                if (reference == fixture.end()) continue;
                const Tensor& expected = reference->second;
                if (is_float_dtype(results[index].dtype) && is_float_dtype(expected.dtype)) {
                    double error_value = max_abs_error(results[index], expected);
                    if (error_value > worst) { worst = error_value; worst_name = graph.outputs[index]; }
                } else if (!exact_equal(results[index], expected)) {
                    ++mismatches;
                }
            }
            std::printf("%-10s iter %d  %6.2fs  worst %.3g (%s)  int mismatches %d\n",
                        tag.c_str(), iteration, seconds, worst, worst_name.c_str(), mismatches);
            if (worst > error_tolerance(results.empty() ? 1 : results[0].dtype) || mismatches) ++failures;
            if (!dump_path.empty() && iteration == iterations - 1) write_captured(graph, dump_path);
        }
    }
    return failures;
}

void write_wav(const char* path, const std::vector<float>& audio, int sample_rate) {
    std::FILE* file = std::fopen(path, "wb");
    if (!file) return;
    uint32_t data_bytes = static_cast<uint32_t>(audio.size() * 2);
    uint32_t chunk = 36 + data_bytes;
    uint16_t channels = 1;
    uint16_t bits = 16;
    uint32_t rate = static_cast<uint32_t>(sample_rate);
    uint32_t byte_rate = rate * channels * bits / 8;
    uint16_t align = channels * bits / 8;
    uint16_t format = 1;
    std::fwrite("RIFF", 1, 4, file); std::fwrite(&chunk, 4, 1, file);
    std::fwrite("WAVEfmt ", 1, 8, file);
    uint32_t fmt_size = 16; std::fwrite(&fmt_size, 4, 1, file);
    std::fwrite(&format, 2, 1, file); std::fwrite(&channels, 2, 1, file);
    std::fwrite(&rate, 4, 1, file); std::fwrite(&byte_rate, 4, 1, file);
    std::fwrite(&align, 2, 1, file); std::fwrite(&bits, 2, 1, file);
    std::fwrite("data", 1, 4, file); std::fwrite(&data_bytes, 4, 1, file);
    for (float value : audio) {
        float clamped = std::fmax(-1.0f, std::fmin(1.0f, value));
        int16_t sample = static_cast<int16_t>(std::lround(clamped * 32767.0f));
        std::fwrite(&sample, 2, 1, file);
    }
    std::fclose(file);
}

Tensor make_tensor(const std::vector<int64_t>& shape, int32_t dtype, std::size_t bytes) {
    Tensor tensor;
    tensor.shape = shape;
    tensor.dtype = dtype;
    tensor.data.assign(bytes, 0);
    return tensor;
}

int run_synth(const std::string& package, const TensorMap& fixture, int max_steps, const std::string& wav_path) {
    std::string error;
    const std::string character = package + "/character";
    const std::string common = package + "/common";

    Graph hubert, speaker, encoder, first, stage, vits;
    if (!hubert.open(common + "/chinese-hubert-base/chinese-hubert-base.onnx", "", error) ||
        !speaker.open(common + "/speaker_encoder.onnx", "", error) ||
        !encoder.open(character + "/t2s_encoder_fp32.onnx", "", error) ||
        !first.open(character + "/t2s_first_stage_decoder_fp32.onnx", character + "/t2s_shared_fp16.bin", error) ||
        !stage.open(character + "/t2s_stage_decoder_fp32.onnx", character + "/t2s_shared_fp16.bin", error) ||
        !vits.open(character + "/vits_v2pro.onnx", character + "/vits_v2pro_fp16.bin", error)) {
        std::printf("open failed: %s\n", error.c_str());
        return 1;
    }

    /* 1. prompt features: HuBERT content + speaker embedding. */
    Timer total;
    std::vector<Tensor> hubert_out;
    {
        std::map<std::string, Tensor> named;
        collect_inputs(fixture, "hubert", named);
        std::vector<const Tensor*> feeds;
        if (!order_feeds(hubert, named, feeds, error)) { std::printf("%s\n", error.c_str()); return 1; }
        Timer timer;
        if (!hubert.run(feeds, hubert_out, error)) { std::printf("hubert: %s\n", error.c_str()); return 1; }
        std::printf("hubert     %6.2fs  %s\n", timer.seconds(), shape_text(hubert_out[0]).c_str());
        auto reference = fixture.find("hubert.out.0");
        if (reference != fixture.end()) std::printf("           vs ORT %.3g\n", max_abs_error(hubert_out[0], reference->second));
    }
    std::vector<Tensor> speaker_out;
    {
        std::map<std::string, Tensor> named;
        collect_inputs(fixture, "speaker", named);
        std::vector<const Tensor*> feeds;
        if (!order_feeds(speaker, named, feeds, error)) { std::printf("%s\n", error.c_str()); return 1; }
        Timer timer;
        if (!speaker.run(feeds, speaker_out, error)) { std::printf("speaker: %s\n", error.c_str()); return 1; }
        std::printf("speaker    %6.2fs  %s\n", timer.seconds(), shape_text(speaker_out[0]).c_str());
        auto reference = fixture.find("speaker.out.0");
        if (reference != fixture.end()) std::printf("           vs ORT %.3g\n", max_abs_error(speaker_out[0], reference->second));
    }

    /* 2. text encoder: swap in our own HuBERT content. */
    std::vector<Tensor> encoder_out;
    {
        std::map<std::string, Tensor> named;
        collect_inputs(fixture, "encoder", named);
        named["ssl_content"] = hubert_out[0];
        std::vector<const Tensor*> feeds;
        if (!order_feeds(encoder, named, feeds, error)) { std::printf("%s\n", error.c_str()); return 1; }
        Timer timer;
        if (!encoder.run(feeds, encoder_out, error)) { std::printf("encoder: %s\n", error.c_str()); return 1; }
        std::printf("encoder    %6.2fs  x %s prompts %s\n", timer.seconds(),
                    shape_text(encoder_out[0]).c_str(), shape_text(encoder_out[1]).c_str());
        auto reference = fixture.find("encoder.out.0");
        if (reference != fixture.end()) std::printf("           vs ORT %.3g\n", max_abs_error(encoder_out[0], reference->second));
    }

    const int64_t prompt_columns = encoder_out[1].elements() / (encoder_out[1].shape[0] > 0 ? encoder_out[1].shape[0] : 1);
    const auto noise_entry = fixture.find("noise.steps");
    if (noise_entry == fixture.end()) { std::printf("fixture has no sampling noise\n"); return 1; }
    const Tensor& noise = noise_entry->second;
    const int64_t noise_rows = noise.shape[0];
    const int64_t noise_width = noise.shape.size() > 1 ? noise.shape[1] : 0;
    if (noise_width != 1025) { std::printf("unexpected sample noise width %lld\n", static_cast<long long>(noise_width)); return 1; }
    const float* noise_data = as_float(noise);

    std::map<std::string, Tensor> first_named;
    collect_inputs(fixture, "first", first_named);
    std::vector<Tensor> sampling_values;
    for (const std::string& name : first.inputs) {
        if (name == "x" || name == "prompts") continue;
        sampling_values.push_back(first_named.at(name));
    }
    /* Keep one mutable copy of sample_noise that we refresh every step. */
    std::size_t noise_slot = sampling_values.size();
    for (std::size_t index = 0; index < sampling_values.size(); ++index) {
        if (sampling_values[index].shape.size() == 1 && sampling_values[index].shape[0] == 1025) noise_slot = index;
    }
    if (noise_slot >= sampling_values.size()) { std::printf("sample_noise input missing\n"); return 1; }

    auto build_sampling = [&](int64_t row) {
        std::vector<Tensor> values = sampling_values;
        Tensor& slot = values[noise_slot];
        std::memcpy(slot.data.data(), noise_data + row * noise_width, static_cast<std::size_t>(noise_width) * sizeof(float));
        return values;
    };

    /* 3. first stage decoder.
       The decoder state is tracked by input name: the first stage returns
       [y, y_emb, present_*] while later steps also return a stop condition,
       so positional indexing silently shifts the KV cache by one. */
    std::map<std::string, Tensor> variables;
    auto adopt = [&](const Graph& graph, std::vector<Tensor>& outputs) {
        for (std::size_t index = 0; index < graph.outputs.size() && index < outputs.size(); ++index) {
            const std::string& name = graph.outputs[index];
            if (name == "y" || name == "y_emb" || name == "stop_condition_tensor") {
                variables[name] = std::move(outputs[index]);
            } else if (name.rfind("present_", 0) == 0) {
                variables["past_" + name.substr(8)] = std::move(outputs[index]);
            }
        }
    };
    {
        std::vector<Tensor> values = build_sampling(0);
        std::vector<const Tensor*> feeds(first.inputs.size(), nullptr);
        int sampling_index = 0;
        for (std::size_t index = 0; index < first.inputs.size(); ++index) {
            if (first.inputs[index] == "x") feeds[index] = &encoder_out[0];
            else if (first.inputs[index] == "prompts") feeds[index] = &encoder_out[1];
            else feeds[index] = &values[sampling_index++];
        }
        std::vector<Tensor> outputs;
        Timer timer;
        if (!first.run(feeds, outputs, error)) { std::printf("first stage: %s\n", error.c_str()); return 1; }
        const Tensor& y = outputs[0];
        std::printf("first      %6.2fs  y %s\n", timer.seconds(), shape_text(y).c_str());
        auto reference = fixture.find("first.out.0");
        if (reference != fixture.end()) {
            std::printf("           vs ORT y %.3g (int exact %s)\n",
                        max_abs_error(y, reference->second),
                        exact_equal(y, reference->second) ? "yes" : "no");
        }
        adopt(first, outputs);
    }

    /* 4. autoregressive semantic decoder. */
    int64_t steps = 0;
    Timer decode_timer;
    for (int step = 0; step < max_steps; ++step) {
        std::vector<Tensor> values = build_sampling(step + 1);
        std::vector<const Tensor*> feeds(stage.inputs.size(), nullptr);
        std::size_t sampling_index = 0;
        for (std::size_t index = 0; index < stage.inputs.size(); ++index) {
            const std::string& name = stage.inputs[index];
            /* The stage decoder names the running state iy/iy_emb. */
            const char* alias = name == "iy" ? "y" : name == "iy_emb" ? "y_emb" : nullptr;
            auto held = variables.find(alias ? alias : name);
            if (held != variables.end()) {
                feeds[index] = &held->second;
            } else if (sampling_index < values.size()) {
                feeds[index] = &values[sampling_index++];
            } else {
                std::printf("stage step %d: no fixture value for input %s\n", step, name.c_str());
                return 1;
            }
        }
        std::vector<Tensor> outputs;
        if (!stage.run(feeds, outputs, error)) { std::printf("stage step %d: %s\n", step, error.c_str()); return 1; }
        adopt(stage, outputs);
        ++steps;
        const Tensor& stop = variables["stop_condition_tensor"];
        if (!stop.data.empty() && stop.data[0] != 0) break;
        const Tensor& y = variables["y"];
        const int64_t* tokens = as_int64(y);
        bool finished = false;
        for (int64_t index = 0; index < y.elements(); ++index) {
            if (tokens[index] >= 1024) { finished = true; break; }
        }
        if (finished) break;
    }
    std::printf("decoder    %6.2fs  %lld steps\n", decode_timer.seconds(), static_cast<long long>(steps));

    /* 5. keep the generated part of the token sequence. */
    const Tensor& tokens_tensor = variables["y"];
    const int64_t* tokens = as_int64(tokens_tensor);
    const int64_t token_count = tokens_tensor.elements();
    int64_t begin = prompt_columns;
    int64_t end = token_count;
    for (int64_t index = begin; index < token_count; ++index) {
        if (tokens[index] >= 1024) { end = index; break; }
    }
    if (end <= begin) { end = begin + 1; }
    Tensor semantic = make_tensor({1, 1, end - begin}, 7, static_cast<std::size_t>(end - begin) * sizeof(int64_t));
    std::memcpy(semantic.data.data(), tokens + begin, static_cast<std::size_t>(end - begin) * sizeof(int64_t));
    std::printf("semantic   %lld tokens (prompt %lld, total %lld)\n",
                static_cast<long long>(end - begin), static_cast<long long>(prompt_columns),
                static_cast<long long>(token_count));

    /* 6. vocoder. */
    std::vector<Tensor> audio_out;
    {
        std::map<std::string, Tensor> named;
        collect_inputs(fixture, "vits", named);
        named["pred_semantic"] = semantic;
        named["speaker_embedding"] = speaker_out[0];
        std::vector<const Tensor*> feeds;
        if (!order_feeds(vits, named, feeds, error)) { std::printf("%s\n", error.c_str()); return 1; }
        Timer timer;
        if (!vits.run(feeds, audio_out, error)) { std::printf("vits: %s\n", error.c_str()); return 1; }
        std::printf("vits       %6.2fs  %s\n", timer.seconds(), shape_text(audio_out[0]).c_str());
    }

    const float* samples = as_float(audio_out[0]);
    int64_t count = audio_out[0].elements();
    std::vector<float> audio(samples, samples + count);
    float peak = 0.0f;
    for (float value : audio) peak = std::fmax(peak, std::fabs(value));
    if (peak > 1.0f) {
        for (float& value : audio) value /= peak;
    }
    double worst = 0.0;
    auto reference = fixture.find("reference.audio");
    if (reference != fixture.end()) {
        const float* expected = as_float(reference->second);
        int64_t reference_count = reference->second.elements();
        int64_t common_count = std::min(count, reference_count);
        for (int64_t index = 0; index < common_count; ++index) {
            worst = std::fmax(worst, std::fabs(static_cast<double>(audio[index]) - expected[index]));
        }
        std::printf("audio      %lld samples (ORT %lld), max abs error vs ORT %.3g\n",
                    static_cast<long long>(count), static_cast<long long>(reference_count), worst);
    }
    if (!wav_path.empty()) {
        write_wav(wav_path.c_str(), audio, 32000);
        std::printf("wrote %s (%.2fs)\n", wav_path.c_str(), static_cast<double>(count) / 32000.0);
    }
    std::printf("total      %6.2fs\n", total.seconds());
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    std::string package;
    std::string fixture_path;
    std::string mode = "replay";
    std::string wav_path;
    std::string only;
    std::string dump_nodes;
    int max_steps = 64;
    int replay_iterations = 2;
    for (int index = 1; index < argc; ++index) {
        std::string flag = argv[index];
        auto next = [&](const char* fallback) -> std::string {
            return index + 1 < argc ? std::string(argv[++index]) : std::string(fallback);
        };
        if (flag == "--package") package = next("");
        else if (flag == "--fixture") fixture_path = next("");
        else if (flag == "--mode") mode = next("replay");
        else if (flag == "--wav") wav_path = next("");
        else if (flag == "--max-steps") max_steps = std::atoi(next("64").c_str());
        else if (flag == "--replay-iterations") replay_iterations = std::atoi(next("2").c_str());
        else if (flag == "--only") only = next("");
        else if (flag == "--dump-nodes") dump_nodes = next("");
        else { std::printf("unknown argument: %s\n", flag.c_str()); return 2; }
    }
    if (package.empty() || fixture_path.empty()) {
        std::printf("usage: fsv_voice_cli --package <dir> --fixture <file> [--mode replay|synth] [--wav out.wav]\n");
        return 2;
    }
    TensorMap fixture;
    std::string error;
    if (!dump_nodes.empty()) _putenv_s("FSV_NATIVE_CAPTURE", "1");
    if (!load_fixture(fixture_path.c_str(), fixture, error)) {
        std::printf("%s\n", error.c_str());
        return 2;
    }
    std::printf("fixture: %zu tensors\n", fixture.size());
    if (mode == "replay") {
        const std::vector<std::pair<std::string, std::string>> graphs = {
            {"g2pw", "/common/G2P/G2PW/g2pW_int4_fp16mix.onnx"},
            {"roberta", "/common/RoBERTa/RoBERTa.onnx"},
            {"hubert", "/common/chinese-hubert-base/chinese-hubert-base.onnx"},
            {"speaker", "/common/speaker_encoder.onnx"},
            {"encoder", "/character/t2s_encoder_fp32.onnx"},
            {"first", "/character/t2s_first_stage_decoder_fp32.onnx"},
            {"stage", "/character/t2s_stage_decoder_fp32.onnx"},
            {"vits", "/character/vits_v2pro.onnx"},
        };
        std::vector<std::pair<std::string, std::string>> selected;
        for (const auto& entry : graphs) {
            if (only.empty() || only == entry.first) selected.push_back(entry);
        }
        int failures = run_replay(package, fixture, selected, replay_iterations, dump_nodes);
        std::printf("%s\n", failures == 0 ? "REPLAY OK" : "REPLAY FAILED");
        return failures == 0 ? 0 : 1;
    }
    return run_synth(package, fixture, max_steps, wav_path);
}
