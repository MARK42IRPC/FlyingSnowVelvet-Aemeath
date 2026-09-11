#include "fsv_unicode.h"

#include "fsv_unicode_tables.h"

namespace fsv {
namespace uni {
namespace {

bool in_ranges(const detail::Range* ranges, unsigned count, uint32_t code_point) {
    unsigned low = 0;
    unsigned high = count;
    while (low < high) {
        unsigned middle = low + (high - low) / 2;
        if (code_point < ranges[middle].first) {
            high = middle;
        } else if (code_point > ranges[middle].last) {
            low = middle + 1;
        } else {
            return true;
        }
    }
    return false;
}

const detail::FoldEntry* find_fold(uint32_t code_point) {
    unsigned low = 0;
    unsigned high = detail::kFoldEntryCount;
    while (low < high) {
        unsigned middle = low + (high - low) / 2;
        const detail::FoldEntry& entry = detail::kFoldEntries[middle];
        if (code_point < entry.code_point) {
            high = middle;
        } else if (code_point > entry.code_point) {
            low = middle + 1;
        } else {
            return &entry;
        }
    }
    return nullptr;
}

/* Hangul syllables have an algorithmic canonical decomposition, so they stay
   out of the generated table. */
const uint32_t HANGUL_SBASE = 0xAC00;
const uint32_t HANGUL_LBASE = 0x1100;
const uint32_t HANGUL_VBASE = 0x1161;
const uint32_t HANGUL_TBASE = 0x11A7;
const uint32_t HANGUL_VCOUNT = 21;
const uint32_t HANGUL_TCOUNT = 28;
const uint32_t HANGUL_NCOUNT = HANGUL_VCOUNT * HANGUL_TCOUNT;
const uint32_t HANGUL_SCOUNT = 19 * HANGUL_NCOUNT;

bool append_hangul(uint32_t code_point, std::string& out) {
    if (code_point < HANGUL_SBASE || code_point >= HANGUL_SBASE + HANGUL_SCOUNT) {
        return false;
    }
    uint32_t index = code_point - HANGUL_SBASE;
    utf8_append(HANGUL_LBASE + index / HANGUL_NCOUNT, out);
    utf8_append(HANGUL_VBASE + (index % HANGUL_NCOUNT) / HANGUL_TCOUNT, out);
    uint32_t trailing = index % HANGUL_TCOUNT;
    if (trailing != 0) {
        utf8_append(HANGUL_TBASE + trailing, out);
    }
    return true;
}

}  // namespace

bool is_control(uint32_t code_point) {
    return in_ranges(detail::kControlRanges, detail::kControlRangeCount, code_point);
}

bool is_space(uint32_t code_point) {
    return in_ranges(detail::kSpaceRanges, detail::kSpaceRangeCount, code_point);
}

bool is_chinese(uint32_t code_point) {
    return in_ranges(detail::kChineseRanges, detail::kChineseRangeCount, code_point);
}

bool is_punctuation(uint32_t code_point) {
    return in_ranges(detail::kPunctuationRanges, detail::kPunctuationRangeCount, code_point);
}

void append_fold(uint32_t code_point, std::string& out) {
    const detail::FoldEntry* entry = find_fold(code_point);
    if (entry != nullptr) {
        out.append(detail::kFoldBlob + entry->offset, entry->length);
        return;
    }
    if (append_hangul(code_point, out)) {
        return;
    }
    utf8_append(code_point, out);
}

void utf8_append(uint32_t code_point, std::string& out) {
    if (code_point <= 0x7F) {
        out.push_back(static_cast<char>(code_point));
    } else if (code_point <= 0x7FF) {
        out.push_back(static_cast<char>(0xC0 | (code_point >> 6)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    } else if (code_point <= 0xFFFF) {
        out.push_back(static_cast<char>(0xE0 | (code_point >> 12)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (code_point >> 18)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((code_point >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (code_point & 0x3F)));
    }
}

void utf8_decode(const std::string& text, std::vector<uint32_t>& out) {
    out.clear();
    out.reserve(text.size());
    size_t index = 0;
    size_t size = text.size();
    while (index < size) {
        unsigned char lead = static_cast<unsigned char>(text[index]);
        uint32_t value = 0;
        size_t extra = 0;
        if (lead < 0x80) {
            value = lead;
        } else if ((lead & 0xE0) == 0xC0) {
            value = lead & 0x1F;
            extra = 1;
        } else if ((lead & 0xF0) == 0xE0) {
            value = lead & 0x0F;
            extra = 2;
        } else if ((lead & 0xF8) == 0xF0) {
            value = lead & 0x07;
            extra = 3;
        } else {
            out.push_back(0xFFFD);
            ++index;
            continue;
        }
        if (index + extra >= size) {
            out.push_back(0xFFFD);
            ++index;
            continue;
        }
        bool valid = true;
        for (size_t step = 1; step <= extra; ++step) {
            unsigned char continuation = static_cast<unsigned char>(text[index + step]);
            if ((continuation & 0xC0) != 0x80) {
                valid = false;
                break;
            }
            value = (value << 6) | (continuation & 0x3F);
        }
        if (!valid) {
            out.push_back(0xFFFD);
            ++index;
            continue;
        }
        out.push_back(value);
        index += extra + 1;
    }
}

}  // namespace uni
}  // namespace fsv
