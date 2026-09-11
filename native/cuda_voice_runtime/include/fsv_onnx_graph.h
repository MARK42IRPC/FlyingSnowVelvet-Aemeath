#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace fsv {

struct TensorShapeDimension {
    bool has_value = false;
    int64_t value = 0;
    std::string param;
};

struct TensorShape {
    std::vector<TensorShapeDimension> dims;
};

struct TensorType {
    int32_t elem_type = 0;
    TensorShape shape;
};

struct ValueInfo {
    std::string name;
    TensorType tensor;
};

struct TensorProto {
    std::vector<int64_t> dims;
    int32_t data_type = 0;
    std::string name;
    std::vector<std::uint8_t> raw_data;
    bool has_raw_data = false;
    int32_t data_location = 0;
    std::string external_location;
    std::string external_offset;
    std::string external_length;
};

struct GraphProto;

struct AttributeProto {
    std::string name;
    int32_t type = 0;
    float f = 0.0f;
    int64_t i = 0;
    std::string s;
    TensorProto tensor;
    std::shared_ptr<GraphProto> graph;
    std::vector<float> floats;
    std::vector<int64_t> ints;
    std::vector<std::string> strings;
    std::vector<TensorProto> tensors;
    std::vector<std::shared_ptr<GraphProto>> graphs;
};

struct NodeProto {
    std::vector<std::string> inputs;
    std::vector<std::string> outputs;
    std::string name;
    std::string op_type;
    std::string domain;
    std::vector<AttributeProto> attributes;
};

struct GraphProto {
    std::vector<NodeProto> nodes;
    std::string name;
    std::vector<TensorProto> initializers;
    std::vector<ValueInfo> inputs;
    std::vector<ValueInfo> outputs;
};

struct OperatorSetId {
    std::string domain;
    int64_t version = 0;
};

struct ModelProto {
    int64_t ir_version = 0;
    std::string producer_name;
    std::string producer_version;
    GraphProto graph;
    std::vector<OperatorSetId> opset_imports;
};

bool parse_onnx_model(const void* data, std::size_t size, ModelProto& out, std::string& error);
bool load_onnx_model(const char* path, ModelProto& out, std::string& error);

}  // namespace fsv
