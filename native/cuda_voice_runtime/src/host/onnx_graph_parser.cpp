#include "fsv_onnx_graph.h"

#include <cstring>
#include <fstream>
#include <stdexcept>
#include <utility>

namespace {

using fsv::AttributeProto;
using fsv::GraphProto;
using fsv::ModelProto;
using fsv::NodeProto;
using fsv::TensorProto;
using fsv::ValueInfo;

class Reader {
public:
    Reader(const std::uint8_t* data, std::size_t size) : begin_(data), pos_(data), end_(data + size) {}

    bool empty() const { return pos_ == end_; }
    std::uint64_t read_varint() {
        std::uint64_t value = 0;
        int shift = 0;
        while (pos_ < end_) {
            std::uint8_t byte = *pos_++;
            if (shift == 63 && (byte & 0x7f) > 1) throw std::runtime_error("protobuf varint overflow");
            value |= static_cast<std::uint64_t>(byte & 0x7f) << shift;
            if ((byte & 0x80) == 0) return value;
            shift += 7;
            if (shift > 63) throw std::runtime_error("protobuf varint too long");
        }
        throw std::runtime_error("truncated protobuf varint");
    }

    std::uint32_t read_fixed32() {
        require(4);
        std::uint32_t value = 0;
        std::memcpy(&value, pos_, sizeof(value));
        pos_ += 4;
        return value;
    }

    std::uint64_t read_fixed64() {
        require(8);
        std::uint64_t value = 0;
        std::memcpy(&value, pos_, sizeof(value));
        pos_ += 8;
        return value;
    }

    Reader read_bytes() {
        std::uint64_t length = read_varint();
        if (length > static_cast<std::uint64_t>(end_ - pos_)) throw std::runtime_error("truncated protobuf bytes");
        Reader next(pos_, static_cast<std::size_t>(length));
        pos_ += static_cast<std::size_t>(length);
        return next;
    }

    std::string read_string() {
        Reader next = read_bytes();
        return std::string(reinterpret_cast<const char*>(next.begin_), static_cast<std::size_t>(next.end_ - next.begin_));
    }

    struct Field {
        std::uint32_t number;
        std::uint8_t wire;
    };

    Field read_field() {
        std::uint64_t tag = read_varint();
        if (tag == 0) throw std::runtime_error("invalid protobuf tag zero");
        return {static_cast<std::uint32_t>(tag >> 3), static_cast<std::uint8_t>(tag & 7)};
    }

    void skip(Field field) {
        switch (field.wire) {
            case 0: read_varint(); break;
            case 1: require(8); pos_ += 8; break;
            case 2: read_bytes(); break;
            case 5: require(4); pos_ += 4; break;
            default: throw std::runtime_error("unsupported protobuf wire type");
        }
    }

    void require(std::size_t count) const {
        if (count > static_cast<std::size_t>(end_ - pos_)) throw std::runtime_error("truncated protobuf fixed value");
    }

private:
    const std::uint8_t* begin_;
    const std::uint8_t* pos_;
    const std::uint8_t* end_;
};

void parse_graph(Reader&, GraphProto&);
void parse_tensor(Reader&, TensorProto&);
void parse_type(Reader&, fsv::TensorType&);

void parse_string_entry(Reader& reader, std::string& key, std::string& value) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        if (field.number == 1 && field.wire == 2) key = reader.read_string();
        else if (field.number == 2 && field.wire == 2) value = reader.read_string();
        else reader.skip(field);
    }
}

void parse_tensor(Reader& reader, TensorProto& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        switch (field.number) {
            case 1:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) out.dims.push_back(static_cast<int64_t>(packed.read_varint()));
                } else if (field.wire == 0) {
                    out.dims.push_back(static_cast<int64_t>(reader.read_varint()));
                } else reader.skip(field);
                break;
            case 2: out.data_type = static_cast<int32_t>(reader.read_varint()); break;
            case 4:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) packed.read_fixed32();
                } else if (field.wire == 5) reader.read_fixed32();
                else reader.skip(field);
                break;
            case 5:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) packed.read_fixed32();
                } else if (field.wire == 5) reader.read_fixed32();
                else reader.skip(field);
                break;
            case 7:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) packed.read_varint();
                } else if (field.wire == 0) reader.read_varint();
                else reader.skip(field);
                break;
            case 8: out.name = reader.read_string(); break;
            case 9: {
                std::string raw = reader.read_string();
                out.raw_data.assign(reinterpret_cast<const std::uint8_t*>(raw.data()),
                                   reinterpret_cast<const std::uint8_t*>(raw.data() + raw.size()));
                out.has_raw_data = true;
                break;
            }
            case 10:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) packed.read_fixed64();
                } else if (field.wire == 1) reader.read_fixed64();
                else reader.skip(field);
                break;
            case 11:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) packed.read_varint();
                } else if (field.wire == 0) reader.read_varint();
                else reader.skip(field);
                break;
            case 13: {
                std::string key;
                std::string value;
                parse_string_entry(reader.read_bytes(), key, value);
                if (key == "location") out.external_location = value;
                else if (key == "offset") out.external_offset = value;
                else if (key == "length") out.external_length = value;
                break;
            }
            case 14: out.data_location = static_cast<int32_t>(reader.read_varint()); break;
            default: reader.skip(field); break;
        }
    }
}

void parse_shape(Reader& reader, fsv::TensorShape& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        if (field.number != 1) {
            reader.skip(field);
            continue;
        }
        Reader dim_reader = reader.read_bytes();
        fsv::TensorShapeDimension dim;
        while (!dim_reader.empty()) {
            Reader::Field dim_field = dim_reader.read_field();
            if (dim_field.number == 1 && dim_field.wire == 0) {
                dim.has_value = true;
                dim.value = static_cast<int64_t>(dim_reader.read_varint());
            } else if (dim_field.number == 2 && dim_field.wire == 2) {
                dim.param = dim_reader.read_string();
            } else {
                dim_reader.skip(dim_field);
            }
        }
        out.dims.push_back(std::move(dim));
    }
}

void parse_type(Reader& reader, fsv::TensorType& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        if (field.number == 1 && field.wire == 2) {
            Reader tensor_reader = reader.read_bytes();
            while (!tensor_reader.empty()) {
                Reader::Field tensor_field = tensor_reader.read_field();
                if (tensor_field.number == 1 && tensor_field.wire == 0) {
                    out.elem_type = static_cast<int32_t>(tensor_reader.read_varint());
                } else if (tensor_field.number == 2 && tensor_field.wire == 2) {
                    parse_shape(tensor_reader.read_bytes(), out.shape);
                } else {
                    tensor_reader.skip(tensor_field);
                }
            }
        } else {
            reader.skip(field);
        }
    }
}

void parse_value_info(Reader& reader, ValueInfo& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        if (field.number == 1 && field.wire == 2) out.name = reader.read_string();
        else if (field.number == 2 && field.wire == 2) parse_type(reader.read_bytes(), out.tensor);
        else reader.skip(field);
    }
}

struct RawAttribute {
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

void parse_attribute(Reader& reader, RawAttribute& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        switch (field.number) {
            case 1: out.name = reader.read_string(); break;
            case 2: {
                std::uint32_t bits = reader.read_fixed32();
                std::memcpy(&out.f, &bits, sizeof(out.f));
                break;
            }
            case 3: out.i = static_cast<int64_t>(reader.read_varint()); break;
            case 4: out.s = reader.read_string(); break;
            case 5: parse_tensor(reader.read_bytes(), out.tensor); break;
            case 6: {
                out.graph = std::make_shared<GraphProto>();
                Reader sub = reader.read_bytes();
                parse_graph(sub, *out.graph);
                break;
            }
            case 7:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) {
                        std::uint32_t bits = packed.read_fixed32();
                        float value = 0.0f;
                        std::memcpy(&value, &bits, sizeof(value));
                        out.floats.push_back(value);
                    }
                } else if (field.wire == 5) {
                    std::uint32_t bits = reader.read_fixed32();
                    float value = 0.0f;
                    std::memcpy(&value, &bits, sizeof(value));
                    out.floats.push_back(value);
                }
                else reader.skip(field);
                break;
            case 8:
                if (field.wire == 2) {
                    Reader packed = reader.read_bytes();
                    while (!packed.empty()) out.ints.push_back(static_cast<int64_t>(packed.read_varint()));
                } else if (field.wire == 0) out.ints.push_back(static_cast<int64_t>(reader.read_varint()));
                else reader.skip(field);
                break;
            case 9:
                if (field.wire == 2) out.strings.push_back(reader.read_string());
                else reader.skip(field);
                break;
            case 10: {
                TensorProto tensor;
                parse_tensor(reader.read_bytes(), tensor);
                out.tensors.push_back(std::move(tensor));
                break;
            }
            case 11: {
                out.graphs.push_back(std::make_shared<GraphProto>());
                Reader sub = reader.read_bytes();
                parse_graph(sub, *out.graphs.back());
                break;
            }
            case 20: out.type = static_cast<int32_t>(reader.read_varint()); break;
            default: reader.skip(field); break;
        }
    }
}

AttributeProto materialize_attribute(RawAttribute raw) {
    AttributeProto out;
    out.name = std::move(raw.name);
    out.type = raw.type;
    out.f = raw.f;
    out.i = raw.i;
    out.s = std::move(raw.s);
    out.tensor = std::move(raw.tensor);
    out.graph = std::move(raw.graph);
    out.floats = std::move(raw.floats);
    out.ints = std::move(raw.ints);
    out.strings = std::move(raw.strings);
    out.tensors = std::move(raw.tensors);
    out.graphs = std::move(raw.graphs);
    return out;
}

void parse_node(Reader& reader, NodeProto& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        switch (field.number) {
            case 1: out.inputs.push_back(reader.read_string()); break;
            case 2: out.outputs.push_back(reader.read_string()); break;
            case 3: out.name = reader.read_string(); break;
            case 4: out.op_type = reader.read_string(); break;
            case 5: {
                RawAttribute raw;
                parse_attribute(reader.read_bytes(), raw);
                out.attributes.push_back(materialize_attribute(std::move(raw)));
                break;
            }
            case 7: out.domain = reader.read_string(); break;
            default: reader.skip(field); break;
        }
    }
}

void parse_graph(Reader& reader, GraphProto& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        switch (field.number) {
            case 1: {
                out.nodes.emplace_back();
                parse_node(reader.read_bytes(), out.nodes.back());
                break;
            }
            case 2: out.name = reader.read_string(); break;
            case 5: {
                out.initializers.emplace_back();
                parse_tensor(reader.read_bytes(), out.initializers.back());
                break;
            }
            case 11: {
                out.inputs.emplace_back();
                parse_value_info(reader.read_bytes(), out.inputs.back());
                break;
            }
            case 12: {
                out.outputs.emplace_back();
                parse_value_info(reader.read_bytes(), out.outputs.back());
                break;
            }
            default: reader.skip(field); break;
        }
    }
}

void parse_model(Reader& reader, ModelProto& out) {
    while (!reader.empty()) {
        Reader::Field field = reader.read_field();
        switch (field.number) {
            case 1: out.ir_version = static_cast<int64_t>(reader.read_varint()); break;
            case 2: out.producer_name = reader.read_string(); break;
            case 3: out.producer_version = reader.read_string(); break;
            case 7: parse_graph(reader.read_bytes(), out.graph); break;
            case 8: {
                Reader sub = reader.read_bytes();
                fsv::OperatorSetId item;
                while (!sub.empty()) {
                    Reader::Field sub_field = sub.read_field();
                    if (sub_field.number == 1 && sub_field.wire == 2) item.domain = sub.read_string();
                    else if (sub_field.number == 2 && sub_field.wire == 0) item.version = static_cast<int64_t>(sub.read_varint());
                    else sub.skip(sub_field);
                }
                out.opset_imports.push_back(std::move(item));
                break;
            }
            default: reader.skip(field); break;
        }
    }
}

}  // namespace

namespace fsv {

bool parse_onnx_model(const void* data, std::size_t size, ModelProto& out, std::string& error) {
    try {
        Reader reader(static_cast<const std::uint8_t*>(data), size);
        parse_model(reader, out);
        return true;
    } catch (const std::exception& exc) {
        error = exc.what();
        return false;
    }
}

bool load_onnx_model(const char* path, ModelProto& out, std::string& error) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        error = std::string("cannot open ONNX model: ") + path;
        return false;
    }
    stream.seekg(0, std::ios::end);
    std::streamoff length = stream.tellg();
    if (length <= 0) {
        error = std::string("empty ONNX model: ") + path;
        return false;
    }
    stream.seekg(0, std::ios::beg);
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));
    stream.read(reinterpret_cast<char*>(bytes.data()), length);
    if (!stream) {
        error = std::string("cannot read ONNX model: ") + path;
        return false;
    }
    return parse_onnx_model(bytes.data(), bytes.size(), out, error);
}

}  // namespace fsv
