#include "fsv_json.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>

namespace fsv {
namespace json {

const Value* Value::find(const std::string& key) const {
    for (const auto& field : fields) {
        if (field.first == key) return &field.second;
    }
    return nullptr;
}

const Value* Value::at(std::size_t index) const {
    return index < items.size() ? &items[index] : nullptr;
}

std::size_t Value::size() const {
    return type == Type::Array ? items.size() : fields.size();
}

std::string Value::as_string(const std::string& fallback) const {
    return type == Type::String ? text : fallback;
}

double Value::as_number(double fallback) const {
    return type == Type::Number ? number : fallback;
}

bool Value::as_bool(bool fallback) const {
    return type == Type::Bool ? boolean : fallback;
}

namespace {

/* Appends one code point as UTF-8. */
void append_utf8(std::string& out, unsigned int code) {
    if (code < 0x80) {
        out.push_back(static_cast<char>(code));
    } else if (code < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (code >> 6)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else if (code < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (code >> 12)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (code >> 18)));
        out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
    }
}

class Parser {
public:
    Parser(const std::string& source) : source_(source) {}

    bool run(Value& out, std::string& error) {
        skip_space();
        if (!parse_value(out)) {
            error = message();
            return false;
        }
        skip_space();
        if (position_ != source_.size()) {
            error = "JSON 末尾存在多余字符";
            return false;
        }
        return true;
    }

private:
    bool parse_value(Value& out) {
        if (position_ >= source_.size()) return fail("JSON 意外结束");
        const char c = source_[position_];
        if (c == '{') return parse_object(out);
        if (c == '[') return parse_array(out);
        if (c == '"') {
            out.type = Value::Type::String;
            return parse_string(out.text);
        }
        if (c == 't') return parse_literal("true", out, Value::Type::Bool) && (out.boolean = true);
        if (c == 'f') return parse_literal("false", out, Value::Type::Bool);
        if (c == 'n') return parse_literal("null", out, Value::Type::Null);
        return parse_number(out);
    }

    bool parse_literal(const char* text, Value& out, Value::Type type) {
        const std::size_t length = std::char_traits<char>::length(text);
        if (source_.compare(position_, length, text) != 0) return fail("JSON 字面量无效");
        position_ += length;
        out.type = type;
        return true;
    }

    bool parse_object(Value& out) {
        out.type = Value::Type::Object;
        ++position_;
        skip_space();
        if (position_ < source_.size() && source_[position_] == '}') {
            ++position_;
            return true;
        }
        while (true) {
            skip_space();
            std::string key;
            if (!parse_string(key)) return false;
            skip_space();
            if (position_ >= source_.size() || source_[position_] != ':') return fail("JSON 缺少冒号");
            ++position_;
            skip_space();
            Value value;
            if (!parse_value(value)) return false;
            out.fields.emplace_back(std::move(key), std::move(value));
            skip_space();
            if (position_ >= source_.size()) return fail("JSON 对象未闭合");
            if (source_[position_] == ',') {
                ++position_;
                continue;
            }
            if (source_[position_] == '}') {
                ++position_;
                return true;
            }
            return fail("JSON 对象分隔符无效");
        }
    }

    bool parse_array(Value& out) {
        out.type = Value::Type::Array;
        ++position_;
        skip_space();
        if (position_ < source_.size() && source_[position_] == ']') {
            ++position_;
            return true;
        }
        while (true) {
            skip_space();
            Value value;
            if (!parse_value(value)) return false;
            out.items.push_back(std::move(value));
            skip_space();
            if (position_ >= source_.size()) return fail("JSON 数组未闭合");
            if (source_[position_] == ',') {
                ++position_;
                continue;
            }
            if (source_[position_] == ']') {
                ++position_;
                return true;
            }
            return fail("JSON 数组分隔符无效");
        }
    }

    bool parse_string(std::string& out) {
        if (position_ >= source_.size() || source_[position_] != '"') return fail("JSON 字符串缺少引号");
        ++position_;
        out.clear();
        while (position_ < source_.size()) {
            const unsigned char c = static_cast<unsigned char>(source_[position_]);
            if (c == '"') {
                ++position_;
                return true;
            }
            if (c != '\\') {
                out.push_back(source_[position_++]);
                continue;
            }
            ++position_;
            if (position_ >= source_.size()) return fail("JSON 转义不完整");
            const char escape = source_[position_++];
            switch (escape) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    unsigned int code = 0;
                    if (!read_hex4(code)) return false;
                    if (code >= 0xD800 && code <= 0xDBFF) {
                        /* A surrogate pair carries one code point. */
                        if (position_ + 1 < source_.size() && source_[position_] == '\\' &&
                            source_[position_ + 1] == 'u') {
                            position_ += 2;
                            unsigned int low = 0;
                            if (!read_hex4(low)) return false;
                            if (low >= 0xDC00 && low <= 0xDFFF) {
                                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                            } else {
                                append_utf8(out, code);
                                code = low;
                            }
                        }
                    }
                    append_utf8(out, code);
                    break;
                }
                default: return fail("JSON 转义字符无效");
            }
        }
        return fail("JSON 字符串未闭合");
    }

    bool read_hex4(unsigned int& out) {
        if (position_ + 4 > source_.size()) return fail("JSON \\u 转义不完整");
        out = 0;
        for (int index = 0; index < 4; ++index) {
            const char c = source_[position_++];
            out <<= 4;
            if (c >= '0' && c <= '9') out |= static_cast<unsigned int>(c - '0');
            else if (c >= 'a' && c <= 'f') out |= static_cast<unsigned int>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') out |= static_cast<unsigned int>(c - 'A' + 10);
            else return fail("JSON \\u 转义含非法字符");
        }
        return true;
    }

    bool parse_number(Value& out) {
        const std::size_t start = position_;
        if (position_ < source_.size() && (source_[position_] == '-' || source_[position_] == '+')) {
            ++position_;
        }
        while (position_ < source_.size()) {
            const char c = source_[position_];
            const bool part = (c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' ||
                              c == '+' || c == '-';
            if (!part) break;
            ++position_;
        }
        if (position_ == start) return fail("JSON 数值无效");
        out.type = Value::Type::Number;
        out.number = std::strtod(source_.substr(start, position_ - start).c_str(), nullptr);
        return true;
    }

    void skip_space() {
        while (position_ < source_.size()) {
            const char c = source_[position_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++position_;
            else break;
        }
    }

    bool fail(const char* reason) {
        char buffer[128];
        std::snprintf(buffer, sizeof(buffer), "%s（偏移 %zu）", reason, position_);
        error_ = buffer;
        return false;
    }

    const std::string& message() const { return error_; }

    const std::string& source_;
    std::size_t position_ = 0;
    std::string error_;
};

}  // namespace

bool parse(const std::string& source, Value& out, std::string& error) {
    Parser parser(source);
    if (!parser.run(out, error)) return false;
    return true;
}

bool load_file(const std::string& path, Value& out, std::string& error) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        error = "无法打开 JSON 文件：" + path;
        return false;
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return parse(buffer.str(), out, error);
}

}  // namespace json
}  // namespace fsv
