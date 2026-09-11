/* Development driver for the self-written text frontend.

   The runtime never shells out to Python, so the tokenizer and (later) the
   grapheme-to-phoneme chain are checked against the reference implementation
   by running both on the same corpus and diffing this tool's output.

   Usage:
     fsv_frontend_cli --tokenizer <tokenizer.json> tokenize <text-file>

   The text file holds one UTF-8 case per line. Each case prints two lines:
     N:<normalised text>
     T:<space separated WordPiece tokens, no special tokens>
     I:<comma separated input ids>
*/

#include "fsv_tokenizer.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

bool read_lines(const char* path, std::vector<std::string>& lines, std::string& error) {
    FILE* handle = std::fopen(path, "rb");
    if (handle == nullptr) {
        error = std::string("cannot open ") + path;
        return false;
    }
    std::string content;
    char buffer[8192];
    size_t read = 0;
    while ((read = std::fread(buffer, 1, sizeof(buffer), handle)) > 0) {
        content.append(buffer, read);
    }
    std::fclose(handle);
    if (content.size() >= 3 && static_cast<unsigned char>(content[0]) == 0xEF &&
        static_cast<unsigned char>(content[1]) == 0xBB &&
        static_cast<unsigned char>(content[2]) == 0xBF) {
        content.erase(0, 3);
    }
    std::string line;
    for (char byte : content) {
        if (byte == '\n') {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            lines.push_back(line);
            line.clear();
        } else {
            line.push_back(byte);
        }
    }
    if (!line.empty()) {
        lines.push_back(line);
    }
    return true;
}

void write_utf8(const std::string& text) {
    std::fwrite(text.data(), 1, text.size(), stdout);
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    // UTF-8 bytes must reach the caller unchanged, so no CRLF translation.
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    std::string tokenizer_path;
    std::string mode;
    std::string text_path;
    for (int index = 1; index < argc; ++index) {
        std::string flag = argv[index];
        auto next = [&](const char* fallback) -> std::string {
            return index + 1 < argc ? std::string(argv[++index]) : std::string(fallback);
        };
        if (flag == "--tokenizer") tokenizer_path = next("");
        else if (flag == "tokenize") mode = flag;
        else if (text_path.empty()) text_path = flag;
        else {
            std::printf("unknown argument: %s\n", flag.c_str());
            return 2;
        }
    }
    if (tokenizer_path.empty() || mode.empty() || text_path.empty()) {
        std::printf("usage: fsv_frontend_cli --tokenizer <tokenizer.json> tokenize <text-file>\n");
        return 2;
    }

    fsv::BertWordPieceTokenizer tokenizer;
    std::string error;
    if (!tokenizer.load(tokenizer_path, error)) {
        std::printf("%s\n", error.c_str());
        return 2;
    }

    std::vector<std::string> lines;
    if (!read_lines(text_path.c_str(), lines, error)) {
        std::printf("%s\n", error.c_str());
        return 2;
    }

    std::string out;
    for (const std::string& line : lines) {
        out.clear();
        out.append("N:");
        out.append(tokenizer.normalize(line));
        out.push_back('\n');
        out.append("T:");
        const std::vector<std::string> tokens = tokenizer.encode_word(line);
        for (size_t index = 0; index < tokens.size(); ++index) {
            if (index != 0) {
                out.push_back(' ');
            }
            out.append(tokens[index]);
        }
        out.push_back('\n');
        out.append("I:");
        const std::vector<int64_t> ids = tokenizer.encode(line);
        for (size_t index = 0; index < ids.size(); ++index) {
            if (index != 0) {
                out.push_back(',');
            }
            out.append(std::to_string(ids[index]));
        }
        out.push_back('\n');
        write_utf8(out);
    }
    return 0;
}
