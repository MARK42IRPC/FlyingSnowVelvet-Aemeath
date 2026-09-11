#pragma once

/* Unicode helpers for the text frontend.

   The voice package's tokenizer.json configures the Hugging Face
   BertNormalizer, whose behaviour is per character: it drops control
   characters, rewrites whitespace to a space, pads CJK with spaces, lowercases
   and strips nonspacing marks. `append_fold` implements the last two steps.

   The classification tables live in the generated fsv_unicode_tables.cpp. */

#include <cstdint>
#include <string>
#include <vector>

namespace fsv {
namespace uni {

bool is_control(uint32_t code_point);
bool is_space(uint32_t code_point);
bool is_chinese(uint32_t code_point);
bool is_punctuation(uint32_t code_point);

/* Lowercase expansion followed by canonical decomposition without nonspacing
   marks. The result can be empty when the code point is a bare combining mark. */
void append_fold(uint32_t code_point, std::string& out);

/* Decode UTF-8. Malformed bytes are replaced with U+FFFD so the caller never
   has to reason about invalid offsets. */
void utf8_decode(const std::string& text, std::vector<uint32_t>& out);
void utf8_append(uint32_t code_point, std::string& out);

}  // namespace uni
}  // namespace fsv
