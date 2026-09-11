#pragma once

/* Minimal JSON reader for the text frontend's data files. The voice package
   ships tokenizer.json and the G2PW dictionaries in JSON, and the runtime has
   no third-party parser to lean on. */

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

namespace fsv {
namespace json {

struct Value {
    enum class Type { Null, Bool, Number, String, Array, Object };

    Type type = Type::Null;
    bool boolean = false;
    double number = 0.0;
    std::string text;
    std::vector<Value> items;
    std::vector<std::pair<std::string, Value>> fields;

    const Value* find(const std::string& key) const;
    const Value* at(std::size_t index) const;
    std::size_t size() const;

    std::string as_string(const std::string& fallback = std::string()) const;
    double as_number(double fallback = 0.0) const;
    bool as_bool(bool fallback = false) const;
};

bool parse(const std::string& source, Value& out, std::string& error);
bool load_file(const std::string& path, Value& out, std::string& error);

}  // namespace json
}  // namespace fsv
