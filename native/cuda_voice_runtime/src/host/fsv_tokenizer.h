#pragma once

/* Hugging Face WordPiece tokenizer reader.

   The voice package ships a tokenizer.json in the format the `tokenizers`
   crate writes: a BertNormalizer, a BertPreTokenizer, a WordPiece model and
   TemplateProcessing with [CLS] ... [SEP]. This class reads the same file and
   reproduces the same ids without the Rust dependency. */

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace fsv {

class BertWordPieceTokenizer {
public:
    /* Reports a Chinese diagnostic in `error` when the file uses a
       configuration the runtime does not implement. */
    bool load(const std::string& tokenizer_json_path, std::string& error);

    /* Full normalisation, matching BertNormalizer. */
    std::string normalize(const std::string& text) const;

    /* WordPiece tokens with the TemplateProcessing special tokens applied. */
    std::vector<int64_t> encode(const std::string& text) const;

    /* WordPiece tokens for one pre-tokenized word, no special tokens. Used by
       the G2PW frontend to align RoBERTa tokens with source characters. */
    std::vector<std::string> encode_word(const std::string& word) const;

    int64_t token_to_id(const std::string& token) const;
    int64_t unknown_id() const { return unknown_id_; }
    int64_t cls_id() const { return cls_id_; }
    int64_t sep_id() const { return sep_id_; }

private:
    std::vector<std::string> pre_tokenize(const std::string& normalized) const;
    std::vector<std::string> word_piece(const std::string& word) const;

    std::unordered_map<std::string, int64_t> vocab_;
    std::string unknown_token_ = "[UNK]";
    std::string continuing_prefix_ = "##";
    size_t max_input_chars_per_word_ = 100;
    int64_t unknown_id_ = -1;
    int64_t cls_id_ = -1;
    int64_t sep_id_ = -1;
    bool clean_text_ = true;
    bool handle_chinese_chars_ = true;
    bool lowercase_ = true;
    bool strip_accents_ = true;
};

}  // namespace fsv
