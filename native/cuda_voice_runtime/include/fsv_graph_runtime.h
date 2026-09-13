#pragma once

#include "fsv_onnx_graph.h"

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace fsv {

/* Tensor payloads are shared between copies. A node loop copies every operand
   it reads and the same weights are read by hundreds of nodes, so copying a
   Tensor must not copy its bytes. Writers detach() first, which clones only
   when the payload is shared. */
class TensorData {
public:
    TensorData() : storage_(empty_storage()) {}

    std::uint8_t* data() { return storage_->data(); }
    const std::uint8_t* data() const { return storage_->data(); }
    std::size_t size() const { return storage_->size(); }
    bool empty() const { return storage_->empty(); }

    void resize(std::size_t bytes) { detach(); storage_->resize(bytes); }
    /* Grows the payload in place so every copy of the tensor sees the bytes.
       Copies of a Tensor are the same value, and the graph runtime relies on
       that when it materialises a device result into a tensor some operator was
       already handed: detaching here would leave that operator reading the
       stale (empty) payload. Writers still detach, so real writes stay private. */
    void resize_shared(std::size_t bytes) {
        /* The empty payload every default-built tensor shares is not this
           tensor's to grow: doing so would hand one buffer to every unrelated
           tensor in the process, which then reads another value's bytes. */
        if (storage_ == empty_storage()) reset_unique();
        storage_->resize(bytes);
    }
    /* Drops the payload for a private empty one. A device result gets its host
       bytes later, and those bytes have to be visible to every copy of the
       tensor without being visible to anybody else. */
    void reset_unique() { storage_ = std::make_shared<std::vector<std::uint8_t>>(); }
    void assign(std::size_t count, std::uint8_t value) { detach(); storage_->assign(count, value); }
    void assign(const std::uint8_t* first, const std::uint8_t* last) {
        detach();
        storage_->assign(first, last);
    }
    void clear() { detach(); storage_->clear(); }

    TensorData& operator=(const std::vector<std::uint8_t>& other) {
        storage_ = std::make_shared<std::vector<std::uint8_t>>(other);
        return *this;
    }

    /* Makes the payload private before it is written through data(). */
    void detach() {
        if (storage_.use_count() > 1) {
            storage_ = std::make_shared<std::vector<std::uint8_t>>(*storage_);
        }
    }

private:
    /* Default-constructed tensors share one empty payload instead of allocating. */
    static const std::shared_ptr<std::vector<std::uint8_t>>& empty_storage() {
        static const std::shared_ptr<std::vector<std::uint8_t>> empty =
            std::make_shared<std::vector<std::uint8_t>>();
        return empty;
    }

    std::shared_ptr<std::vector<std::uint8_t>> storage_;
};

/* Device residency for one tensor payload. The host payload stays the
   authoritative copy for the CPU operators; a CUDA operator can instead leave
   its result in the device buffer and mark the host copy stale, so a chain of
   CUDA operators never round-trips through system memory. Copies of a Tensor
   share this handle exactly like they share the host payload. */
struct TensorDeviceStorage;

struct Tensor {
    std::vector<int64_t> shape;
    int32_t dtype = 0;
    TensorData data;
    std::shared_ptr<TensorDeviceStorage> device;

    int64_t numel() const;
    std::size_t byte_size() const;
};

struct Value {
    std::shared_ptr<Tensor> tensor;
    std::vector<Value> sequence;
    bool is_sequence = false;
    int32_t sequence_dtype = 0;
};

class GraphRuntime {
public:
    GraphRuntime(ModelProto model, std::string model_dir, std::string external_weights);
    ~GraphRuntime();
    GraphRuntime(const GraphRuntime&) = delete;
    GraphRuntime& operator=(const GraphRuntime&) = delete;
    bool prepare(std::string& error);
    bool run(const std::unordered_map<std::string, Tensor>& feeds,
             std::unordered_map<std::string, Tensor>& outputs, std::string& error);
    std::vector<std::string> output_names() const;
    std::vector<std::string> input_names() const;
    /* Keeps every intermediate of the next run for the comparison tools. */
    void set_capture(bool enabled) { capture_ = enabled; }
    /* Declares that a run's output named ``first`` must not be read back to the
       host but kept on the card, and that the following run reads it as its
       input named ``second``. The autoregressive KV cache is what this exists
       for: those tensors are never looked at on the host, they are only handed
       back, and shuffling them through system memory was a third of the total
       transfer volume. A caller that does not feed ``second`` gets the retained
       buffer instead; a caller that does feed it wins, which is how the first
       step of a decode loop seeds the cache. */
    void set_resident(const std::vector<std::pair<std::string, std::string>>& pairs);
    /* True when the named output was declared resident, so the caller should
       expect an empty host payload and read the device buffer instead. */
    bool resident_output(const std::string& name) const;
    /* Host view of a retained output, or null before it has been produced. */
    const Tensor* find_resident(const std::string& name) const;

private:
    ModelProto model_;
    std::string model_dir_;
    std::string external_weights_path_;
    std::unordered_map<std::string, Tensor> initializers_;
    std::unordered_map<std::string, std::string> external_files_;
    std::unordered_map<std::string, Tensor> captured_;
    /* Retained outputs, and for each input the output that feeds it. */
    std::unordered_map<std::string, Tensor> resident_;
    std::unordered_map<std::string, std::string> resident_feeder_;
    std::vector<std::string> resident_names_;
    bool constants_registered_ = false;
    bool capture_ = false;
    /* Every run writes its values into this one table. The plan maps a node's
       operand and result names onto slots in it, so the node loop does not
       hash a string per operand, and building the table costs a pointer store
       per slot instead of an insertion per name. Both are built once, in
       prepare(); see build_plan(). */
    struct Plan;
    std::unordered_map<std::string, Value> values_;
    std::unique_ptr<Plan> plan_;
    bool build_plan(std::string& error);

public:
    const Tensor* find_captured(const std::string& name) const;
    std::vector<std::string> captured_names() const;
};

}  // namespace fsv
