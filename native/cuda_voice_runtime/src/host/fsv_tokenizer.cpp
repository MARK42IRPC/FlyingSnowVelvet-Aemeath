#include "fsv_tokenizer.h"

#include "fsv_json.h"
#include "fsv_unicode.h"

namespace fsv {
namespace {

size_t last_char_length(const std::string& text, size_t end) {
    size_t index = end;
    while (index > 0 && (static_cast<unsigned char>(text[index - 1]) & 0xC0) == 0x80) {
        --index;
    }
    if (index > 0) {
        --index;
    }
    return end - index;
}

size_t char_count(const std::string& text) {
    size_t count = 0;
    for (char byte : text) {
        if ((static_cast<unsigned char>(byte) & 0xC0) != 0x80) {
            ++count;
        }
    }
    return count;
}

std::string type_of(const json::Value& value) {
    const json::Value* type = value.find("type");
    return type == nullptr ? std::string() : type->as_string();
}

bool optional_bool(const json::Value& value, const char* key, bool fallback) {
    const json::Value* entry = value.find(key);
    return entry == nullptr ? fallback : entry->as_bool(fallback);
}

}  // namespace

bool BertWordPieceTokenizer::load(const std::string& tokenizer_json_path, std::string& error) {
    json::Value root;
    if (!json::load_file(tokenizer_json_path, root, error)) {
        return false;
    }

    const json::Value* normalizer = root.find("normalizer");
    if (normalizer == nullptr || type_of(*normalizer) != "BertNormalizer") {
        error = "tokenizer.json: 只支持 BertNormalizer 归一化器";
        return false;
    }
    clean_text_ = optional_bool(*normalizer, "clean_text", true);
    handle_chinese_chars_ = optional_bool(*normalizer, "handle_chinese_chars", true);
    lowercase_ = optional_bool(*normalizer, "lowercase", true);
    const json::Value* strip = normalizer->find("strip_accents");
    strip_accents_ = (strip == nullptr || strip->type == json::Value::Type::Null)
                         ? lowercase_
                         : strip->as_bool(lowercase_);
    if (!clean_text_ || !lowercase_ || !strip_accents_) {
        error = "tokenizer.json: 自研归一化器只实现了 clean_text/lowercase/strip_accents 全开的配置";
        return false;
    }

    const json::Value* pre_tokenizer = root.find("pre_tokenizer");
    if (pre_tokenizer == nullptr || type_of(*pre_tokenizer) != "BertPreTokenizer") {
        error = "tokenizer.json: 只支持 BertPreTokenizer 预分词器";
        return false;
    }

    const json::Value* model = root.find("model");
    if (model == nullptr || type_of(*model) != "WordPiece") {
        error = "tokenizer.json: 只支持 WordPiece 模型";
        return false;
    }
    const json::Value* vocab = model->find("vocab");
    if (vocab == nullptr || vocab->type != json::Value::Type::Object) {
        error = "tokenizer.json: WordPiece 词表缺失";
        return false;
    }
    const json::Value* unknown_token = model->find("unk_token");
    unknown_token_ = unknown_token == nullptr ? std::string("[UNK]") : unknown_token->as_string();
    const json::Value* prefix = model->find("continuing_subword_prefix");
    continuing_prefix_ = prefix == nullptr ? std::string("##") : prefix->as_string();
    const json::Value* max_chars = model->find("max_input_chars_per_word");
    if (max_chars != nullptr) {
        max_input_chars_per_word_ = static_cast<size_t>(max_chars->as_number(100.0));
    }
    vocab_.clear();
    vocab_.reserve(vocab->fields.size() * 2);
    for (const auto& field : vocab->fields) {
        vocab_.emplace(field.first, static_cast<int64_t>(field.second.as_number(0.0)));
    }
    const auto unknown = vocab_.find(unknown_token_);
    if (unknown == vocab_.end()) {
        error = "tokenizer.json: 词表缺少 " + unknown_token_;
        return false;
    }
    unknown_id_ = unknown->second;

    const json::Value* post = root.find("post_processor");
    if (post == nullptr || type_of(*post) != "TemplateProcessing") {
        error = "tokenizer.json: 只支持 TemplateProcessing 后处理器";
        return false;
    }
    const json::Value* single = post->find("single");
    const json::Value* specials = post->find("special_tokens");
    if (single == nullptr || specials == nullptr || single->size() != 3) {
        error = "tokenizer.json: 只支持 [CLS] A [SEP] 模板";
        return false;
    }
    auto special_id = [&](const std::string& token, int64_t& target) {
        const json::Value* entry = specials->find(token);
        if (entry == nullptr) {
            return false;
        }
        const json::Value* ids = entry->find("ids");
        if (ids == nullptr || ids->size() == 0) {
            return false;
        }
        target = static_cast<int64_t>(ids->at(0)->as_number(-1.0));
        return true;
    };
    if (!special_id("[CLS]", cls_id_) || !special_id("[SEP]", sep_id_)) {
        error = "tokenizer.json: 模板缺少 [CLS] 或 [SEP]";
        return false;
    }
    return true;
}

std::string BertWordPieceTokenizer::normalize(const std::string& text) const {
    std::vector<uint32_t> points;
    uni::utf8_decode(text, points);
    std::string out;
    out.reserve(text.size() + 8);
    for (uint32_t code_point : points) {
        if (clean_text_) {
            if (uni::is_control(code_point)) {
                continue;
            }
            if (uni::is_space(code_point)) {
                out.push_back(' ');
                continue;
            }
        }
        bool chinese = handle_chinese_chars_ && uni::is_chinese(code_point);
        if (chinese) {
            out.push_back(' ');
        }
        uni::append_fold(code_point, out);
        if (chinese) {
            out.push_back(' ');
        }
    }
    return out;
}

std::vector<std::string> BertWordPieceTokenizer::pre_tokenize(const std::string& normalized) const {
    std::vector<std::string> words;
    std::string current;
    std::vector<uint32_t> points;
    uni::utf8_decode(normalized, points);
    for (uint32_t code_point : points) {
        if (code_point == ' ') {
            if (!current.empty()) {
                words.push_back(current);
                current.clear();
            }
            continue;
        }
        if (uni::is_punctuation(code_point)) {
            if (!current.empty()) {
                words.push_back(current);
                current.clear();
            }
            std::string piece;
            uni::utf8_append(code_point, piece);
            words.push_back(piece);
            continue;
        }
        uni::utf8_append(code_point, current);
    }
    if (!current.empty()) {
        words.push_back(current);
    }
    return words;
}

std::vector<std::string> BertWordPieceTokenizer::word_piece(const std::string& word) const {
    std::vector<std::string> result;
    if (char_count(word) > max_input_chars_per_word_) {
        result.push_back(unknown_token_);
        return result;
    }
    size_t start = 0;
    while (start < word.size()) {
        size_t end = word.size();
        bool matched = false;
        std::string current;
        while (start < end) {
            std::string candidate;
            if (start > 0) {
                candidate.append(continuing_prefix_);
            }
            candidate.append(word, start, end - start);
            if (vocab_.find(candidate) != vocab_.end()) {
                current = candidate;
                matched = true;
                break;
            }
            end -= last_char_length(word, end);
        }
        if (!matched) {
            result.clear();
            result.push_back(unknown_token_);
            return result;
        }
        result.push_back(current);
        start = end;
    }
    return result;
}

std::vector<std::string> BertWordPieceTokenizer::encode_word(const std::string& word) const {
    std::vector<std::string> tokens;
    for (const std::string& piece : pre_tokenize(normalize(word))) {
        std::vector<std::string> sub = word_piece(piece);
        tokens.insert(tokens.end(), sub.begin(), sub.end());
    }
    return tokens;
}

std::vector<int64_t> BertWordPieceTokenizer::encode(const std::string& text) const {
    std::vector<int64_t> ids;
    ids.push_back(cls_id_);
    for (const std::string& piece : pre_tokenize(normalize(text))) {
        for (const std::string& token : word_piece(piece)) {
            const auto found = vocab_.find(token);
            ids.push_back(found == vocab_.end() ? unknown_id_ : found->second);
        }
    }
    ids.push_back(sep_id_);
    return ids;
}

int64_t BertWordPieceTokenizer::token_to_id(const std::string& token) const {
    const auto found = vocab_.find(token);
    return found == vocab_.end() ? -1 : found->second;
}

}  // namespace fsv
