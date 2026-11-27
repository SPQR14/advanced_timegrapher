// Dynamic libsndfile audio file I/O and WAV/FLAC tag handling (utils/audio_files.py).
// Loads libsndfile at runtime (dlopen/LoadLibraryW) so libadvtg_native keeps zero DT_NEEDED
// or PE import dependency on libsndfile.
#include "advtg.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#ifndef ADVTG_WITH_AUDIO_IO
#  define ADVTG_WITH_AUDIO_IO 1
#endif

#if defined(_WIN32)
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <dlfcn.h>
#endif

namespace fs = std::filesystem;

namespace {

thread_local std::string g_audio_error;

void set_err(const std::string& msg) { g_audio_error = msg; }

#if ADVTG_WITH_AUDIO_IO
fs::path u8_path(const char* utf8) {
    if (!utf8) return {};
    return fs::path(reinterpret_cast<const char8_t*>(utf8));
}

std::string path_u8(const fs::path& p) {
    auto u = p.u8string();
    return std::string(reinterpret_cast<const char*>(u.data()), u.size());
}

// ------------------------------------------------------------------ UTF-8 & comment escaping
void append_utf8(std::string& out, std::uint32_t cp) {
    if (cp <= 0x7FU) {
        out.push_back(static_cast<char>(cp));
    } else if (cp <= 0x7FFU) {
        out.push_back(static_cast<char>(0xC0U | (cp >> 6)));
        out.push_back(static_cast<char>(0x80U | (cp & 0x3FU)));
    } else if (cp <= 0xFFFFU) {
        out.push_back(static_cast<char>(0xE0U | (cp >> 12)));
        out.push_back(static_cast<char>(0x80U | ((cp >> 6) & 0x3FU)));
        out.push_back(static_cast<char>(0x80U | (cp & 0x3FU)));
    } else if (cp <= 0x10FFFFU) {
        out.push_back(static_cast<char>(0xF0U | (cp >> 18)));
        out.push_back(static_cast<char>(0x80U | ((cp >> 12) & 0x3FU)));
        out.push_back(static_cast<char>(0x80U | ((cp >> 6) & 0x3FU)));
        out.push_back(static_cast<char>(0x80U | (cp & 0x3FU)));
    }
}

std::string escape_comment(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    const std::size_t n = text.size();
    for (std::size_t i = 0; i < n;) {
        const unsigned char b = static_cast<unsigned char>(text[i]);
        if (b < 0x80U) {
            out.push_back(static_cast<char>(b));
            ++i;
            continue;
        }
        std::uint32_t cp = 0;
        std::size_t adv = 1;
        if ((b & 0xE0U) == 0xC0U && i + 1 < n) {
            cp = ((b & 0x1FU) << 6) | (static_cast<unsigned char>(text[i + 1]) & 0x3FU);
            adv = 2;
        } else if ((b & 0xF0U) == 0xE0U && i + 2 < n) {
            cp = ((b & 0x0FU) << 12) | ((static_cast<unsigned char>(text[i + 1]) & 0x3FU) << 6) |
                 (static_cast<unsigned char>(text[i + 2]) & 0x3FU);
            adv = 3;
        } else if ((b & 0xF8U) == 0xF0U && i + 3 < n) {
            cp = ((b & 0x07U) << 18) | ((static_cast<unsigned char>(text[i + 1]) & 0x3FU) << 12) |
                 ((static_cast<unsigned char>(text[i + 2]) & 0x3FU) << 6) |
                 (static_cast<unsigned char>(text[i + 3]) & 0x3FU);
            adv = 4;
        } else {
            cp = b;
            adv = 1;
        }
        char buf[16];
        if (cp <= 0xFFU) {
            std::snprintf(buf, sizeof(buf), "\\x%02x", cp);
        } else if (cp <= 0xFFFFU) {
            std::snprintf(buf, sizeof(buf), "\\u%04x", cp);
        } else {
            std::snprintf(buf, sizeof(buf), "\\U%08x", cp);
        }
        out += buf;
        i += adv;
    }
    return out;
}

int hex_val(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

std::string unescape_comment(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    const std::size_t n = text.size();
    for (std::size_t i = 0; i < n;) {
        if (text[i] == '\\' && i + 1 < n) {
            std::size_t digits = 0;
            if (text[i + 1] == 'x') digits = 2;
            else if (text[i + 1] == 'u') digits = 4;
            else if (text[i + 1] == 'U') digits = 8;
            if (digits > 0 && i + 2 + digits <= n) {
                std::uint32_t cp = 0;
                bool ok = true;
                for (std::size_t j = 0; j < digits; ++j) {
                    const int v = hex_val(text[i + 2 + j]);
                    if (v < 0) {
                        ok = false;
                        break;
                    }
                    cp = (cp << 4) | static_cast<std::uint32_t>(v);
                }
                if (ok) {
                    append_utf8(out, cp);
                    i += 2 + digits;
                    continue;
                }
            }
        }
        out.push_back(text[i]);
        ++i;
    }
    return out;
}
#endif  // ADVTG_WITH_AUDIO_IO

// ------------------------------------------------------------------ tag validation & WAV comments
bool valid_tag_key(const std::string& k) {
    if (k.empty()) return false;
    if (!(k[0] >= 'A' && k[0] <= 'Z')) return false;
    for (std::size_t i = 1; i < k.size(); ++i) {
        const char c = k[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return true;
}

std::string to_upper_ascii(std::string s) {
    for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

struct TagEntry {
    std::string key;
    bool has_value = false;
    std::string value;
};

bool check_tags(const advtg_audio_tag* tags, size_t n_tags, std::vector<TagEntry>& out) {
    out.clear();
    if (n_tags == 0) return true;
    if (!tags) {
        set_err("etiquetas nulas");
        return false;
    }
    std::map<std::string, std::size_t> index_by_key;
    for (size_t i = 0; i < n_tags; ++i) {
        if (!tags[i].key) {
            set_err("clave de etiqueta nula");
            return false;
        }
        std::string k = to_upper_ascii(tags[i].key);
        if (!valid_tag_key(k)) {
            set_err("clave de etiqueta no válida: '" + std::string(tags[i].key) +
                    "' (usa [A-Z][A-Z0-9_]*)");
            return false;
        }
        TagEntry e;
        e.key = k;
        if (tags[i].value != nullptr) {
            e.has_value = true;
            e.value = tags[i].value;
            if (e.value.find('\n') != std::string::npos ||
                e.value.find('\r') != std::string::npos) {
                set_err("la etiqueta " + k + " no puede contener saltos de línea");
                return false;
            }
        }
        auto it = index_by_key.find(k);
        if (it == index_by_key.end()) {
            index_by_key[k] = out.size();
            out.push_back(std::move(e));
        } else {
            out[it->second] = std::move(e);
        }
    }
    return true;
}

#if ADVTG_WITH_AUDIO_IO
void split_wav_comment(const std::string& text, std::string& rest_out, bool& has_rest_out,
                       std::vector<std::pair<std::string, std::string>>& tags_out) {
    tags_out.clear();
    has_rest_out = false;
    rest_out.clear();
    if (text.empty()) return;
    std::vector<std::string> lines;
    std::size_t start = 0;
    while (start <= text.size()) {
        std::size_t pos = text.find('\n', start);
        if (pos == std::string::npos) pos = text.size();
        const std::string line = text.substr(start, pos - start);
        const std::size_t eq = line.find('=');
        if (eq != std::string::npos && valid_tag_key(line.substr(0, eq))) {
            const std::string k = line.substr(0, eq);
            const std::string v = line.substr(eq + 1);
            bool replaced = false;
            for (auto& kv : tags_out) {
                if (kv.first == k) {
                    kv.second = v;
                    replaced = true;
                    break;
                }
            }
            if (!replaced) tags_out.emplace_back(k, v);
        } else {
            lines.push_back(line);
        }
        if (pos == text.size()) break;
        start = pos + 1;
    }
    std::string joined;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        if (i) joined.push_back('\n');
        joined += lines[i];
    }
    std::size_t a = 0;
    while (a < joined.size() && joined[a] == '\n') ++a;
    std::size_t b = joined.size();
    while (b > a && joined[b - 1] == '\n') --b;
    if (b > a) {
        rest_out = joined.substr(a, b - a);
        has_rest_out = true;
    }
}

std::string join_wav_comment(const char* comment, const std::vector<TagEntry>& tags) {
    std::string out;
    if (comment && *comment) {
        out = comment;
    }
    for (const auto& t : tags) {
        if (!t.has_value) continue;
        if (!out.empty()) out.push_back('\n');
        out += t.key;
        out.push_back('=');
        out += t.value;
    }
    return out;
}

// ------------------------------------------------------------------ FLAC metadata Vorbis comments
constexpr std::uint32_t kFlacMaxBlock = (1U << 24) - 1U;
constexpr std::uint8_t kFlacStreamInfo = 0;
constexpr std::uint8_t kFlacVorbisComment = 4;

std::uint32_t rd_le32(const std::uint8_t* p) {
    return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
           (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

void wr_le32(std::vector<std::uint8_t>& out, std::uint32_t v) {
    out.push_back(static_cast<std::uint8_t>(v & 0xFFU));
    out.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFFU));
    out.push_back(static_cast<std::uint8_t>((v >> 16) & 0xFFU));
    out.push_back(static_cast<std::uint8_t>((v >> 24) & 0xFFU));
}

struct FlacBlock {
    std::uint8_t type = 0;
    std::vector<std::uint8_t> body;
};

bool read_flac_blocks(std::ifstream& f, std::vector<FlacBlock>& blocks, std::string& err) {
    char magic[4] = {};
    if (!f.read(magic, 4) || std::memcmp(magic, "fLaC", 4) != 0) {
        err = "no empieza por 'fLaC' (¿etiqueta ID3 delante?)";
        return false;
    }
    blocks.clear();
    while (true) {
        std::uint8_t head[4] = {};
        if (!f.read(reinterpret_cast<char*>(head), 4)) {
            err = "cabecera de metadatos FLAC truncada";
            return false;
        }
        const std::uint32_t len = (static_cast<std::uint32_t>(head[1]) << 16) |
                                  (static_cast<std::uint32_t>(head[2]) << 8) |
                                  static_cast<std::uint32_t>(head[3]);
        FlacBlock blk;
        blk.type = head[0] & 0x7FU;
        blk.body.resize(len);
        if (len > 0 && !f.read(reinterpret_cast<char*>(blk.body.data()), len)) {
            err = "bloque de metadatos FLAC truncado";
            return false;
        }
        blocks.push_back(std::move(blk));
        if (head[0] & 0x80U) return true;
    }
}

bool parse_vorbis(const std::vector<std::uint8_t>& body, std::string& vendor,
                  std::vector<std::string>& comments, std::string& err) {
    auto u32 = [&](std::size_t i, std::uint32_t& val) -> bool {
        if (i + 4 > body.size()) {
            err = "comentario Vorbis truncado";
            return false;
        }
        val = rd_le32(body.data() + i);
        return true;
    };
    std::uint32_t n = 0;
    if (!u32(0, n)) return false;
    if (4ULL + n > body.size()) {
        err = "comentario Vorbis truncado";
        return false;
    }
    vendor.assign(reinterpret_cast<const char*>(body.data() + 4), n);
    std::size_t idx = 4 + n;
    std::uint32_t count = 0;
    if (!u32(idx, count)) return false;
    idx += 4;
    if (count > (body.size() - idx) / 4) {
        err = "el conteo de comentarios Vorbis excede el tamaño del bloque";
        return false;
    }
    comments.clear();
    comments.reserve(count);
    for (std::uint32_t c = 0; c < count; ++c) {
        if (!u32(idx, n)) return false;
        idx += 4;
        if (static_cast<std::uint64_t>(idx) + n > body.size()) {
            err = "comentario Vorbis truncado";
            return false;
        }
        comments.emplace_back(reinterpret_cast<const char*>(body.data() + idx), n);
        idx += n;
    }
    return true;
}

std::vector<std::uint8_t> build_vorbis(const std::string& vendor,
                                       const std::vector<std::string>& comments) {
    std::vector<std::uint8_t> out;
    wr_le32(out, static_cast<std::uint32_t>(vendor.size()));
    out.insert(out.end(), vendor.begin(), vendor.end());
    wr_le32(out, static_cast<std::uint32_t>(comments.size()));
    for (const auto& c : comments) {
        wr_le32(out, static_cast<std::uint32_t>(c.size()));
        out.insert(out.end(), c.begin(), c.end());
    }
    return out;
}

fs::path make_temp_path(const fs::path& target) {
    static std::atomic<std::uint64_t> counter{0};
    std::error_code ec;
    fs::path dir = target.parent_path();
    if (!dir.empty()) fs::create_directories(dir, ec);
    const std::uint64_t seq = ++counter;
    const std::string stem = path_u8(target.stem());
    const std::string ext = path_u8(target.extension());
    for (int attempt = 0; attempt < 64; ++attempt) {
        const std::string name = "." + stem + "." + std::to_string(seq) + "_" +
                                 std::to_string(attempt) + ext;
        fs::path cand = dir.empty() ? fs::path(reinterpret_cast<const char8_t*>(name.c_str()))
                                    : (dir / fs::path(reinterpret_cast<const char8_t*>(name.c_str())));
        if (!fs::exists(cand, ec)) return cand;
    }
    return target;
}

bool atomic_replace(const fs::path& tmp, const fs::path& dst, std::string& err) {
#if defined(_WIN32)
    if (MoveFileExW(tmp.c_str(), dst.c_str(),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        return true;
    }
    err = "MoveFileExW failed (" + std::to_string(GetLastError()) + ")";
    return false;
#else
    std::error_code ec;
    fs::rename(tmp, dst, ec);
    if (ec) {
        err = ec.message();
        return false;
    }
    return true;
#endif
}

bool flac_read_tags_file(const fs::path& path,
                         std::vector<std::pair<std::string, std::string>>& out, std::string& err) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        err = "no se pudo abrir";
        return false;
    }
    std::vector<FlacBlock> blocks;
    if (!read_flac_blocks(f, blocks, err)) return false;
    out.clear();
    for (const auto& b : blocks) {
        if (b.type != kFlacVorbisComment) continue;
        std::string vendor;
        std::vector<std::string> comments;
        if (!parse_vorbis(b.body, vendor, comments, err)) return false;
        for (const auto& c : comments) {
            const std::size_t eq = c.find('=');
            if (eq == std::string::npos) continue;
            std::string key = to_upper_ascii(c.substr(0, eq));
            std::string val = unescape_comment(c.substr(eq + 1));
            bool replaced = false;
            for (auto& kv : out) {
                if (kv.first == key) {
                    kv.second = val;
                    replaced = true;
                    break;
                }
            }
            if (!replaced) out.emplace_back(std::move(key), std::move(val));
        }
    }
    return true;
}

bool flac_set_tags_file(const fs::path& path, const std::vector<TagEntry>& tags, std::string& err) {
    fs::path tmp = make_temp_path(path);
    auto cleanup = [&]() {
        std::error_code ec;
        fs::remove(tmp, ec);
    };
    {
        std::ifstream src(path, std::ios::binary);
        if (!src) {
            err = "no se pudo abrir";
            return false;
        }
        std::vector<FlacBlock> blocks;
        if (!read_flac_blocks(src, blocks, err)) return false;
        std::size_t idx = blocks.size();
        for (std::size_t i = 0; i < blocks.size(); ++i) {
            if (blocks[i].type == kFlacVorbisComment) {
                idx = i;
                break;
            }
        }
        std::string vendor = "adv_tg";
        std::vector<std::string> comments;
        if (idx == blocks.size()) {
            idx = (!blocks.empty() && blocks[0].type == kFlacStreamInfo) ? 1 : 0;
            blocks.insert(blocks.begin() + static_cast<std::ptrdiff_t>(idx),
                          FlacBlock{kFlacVorbisComment, {}});
        } else {
            if (!parse_vorbis(blocks[idx].body, vendor, comments, err)) return false;
        }
        std::vector<std::string> filtered;
        filtered.reserve(comments.size() + tags.size());
        for (const auto& c : comments) {
            const std::size_t eq = c.find('=');
            const std::string k = to_upper_ascii(eq == std::string::npos ? c : c.substr(0, eq));
            bool touched = false;
            for (const auto& t : tags) {
                if (t.key == k) {
                    touched = true;
                    break;
                }
            }
            if (!touched) filtered.push_back(c);
        }
        for (const auto& t : tags) {
            if (t.has_value) filtered.push_back(t.key + "=" + t.value);
        }
        std::vector<std::uint8_t> body = build_vorbis(vendor, filtered);
        if (body.size() > kFlacMaxBlock) {
            err = "las etiquetas no caben en un bloque de metadatos FLAC";
            return false;
        }
        blocks[idx].body = std::move(body);

        std::ofstream dst(tmp, std::ios::binary | std::ios::trunc);
        if (!dst) {
            err = "no se pudo crear el archivo temporal";
            cleanup();
            return false;
        }
        dst.write("fLaC", 4);
        for (std::size_t i = 0; i < blocks.size(); ++i) {
            const std::uint8_t last = (i + 1 == blocks.size()) ? 0x80U : 0U;
            const std::uint32_t len = static_cast<std::uint32_t>(blocks[i].body.size());
            const std::uint8_t hdr[4] = {
                static_cast<std::uint8_t>(last | blocks[i].type),
                static_cast<std::uint8_t>((len >> 16) & 0xFFU),
                static_cast<std::uint8_t>((len >> 8) & 0xFFU),
                static_cast<std::uint8_t>(len & 0xFFU),
            };
            dst.write(reinterpret_cast<const char*>(hdr), 4);
            if (len > 0) dst.write(reinterpret_cast<const char*>(blocks[i].body.data()), len);
        }
        std::vector<char> buf(1 << 16);
        while (src) {
            src.read(buf.data(), static_cast<std::streamsize>(buf.size()));
            const std::streamsize got = src.gcount();
            if (got > 0) dst.write(buf.data(), got);
        }
        dst.flush();
        if (!dst) {
            err = "error al escribir el archivo FLAC temporal";
            cleanup();
            return false;
        }
    }
    if (!atomic_replace(tmp, path, err)) {
        cleanup();
        return false;
    }
    return true;
}

// ------------------------------------------------------------------ libsndfile dynamic ABI

constexpr int kSfFormatWav = 0x010000;
constexpr int kSfFormatAiff = 0x020000;
constexpr int kSfFormatOgg = 0x200000;
constexpr int kSfFormatFlac = 0x170000;
constexpr int kSfFormatCaf = 0x130000;
constexpr int kSfFormatRf64 = 0x210000;
constexpr int kSfFormatTypemask = 0x0FFF0000;
constexpr int kSfFormatSubmask = 0x0000FFFF;

constexpr int kSfFormatPcmS8 = 0x0001;
constexpr int kSfFormatPcm16 = 0x0002;
constexpr int kSfFormatPcm24 = 0x0003;
constexpr int kSfFormatPcm32 = 0x0004;
constexpr int kSfFormatPcmU8 = 0x0005;
constexpr int kSfFormatFloat = 0x0006;
constexpr int kSfFormatDouble = 0x0007;
constexpr int kSfFormatVorbis = 0x0060;
constexpr int kSfFormatOpus = 0x0064;

constexpr int kSfmRead = 0x10;
constexpr int kSfmWrite = 0x20;
constexpr int kSfStrComment = 0x05;
constexpr int kSeekSet = 0;

struct SfInfo {
    std::int64_t frames;
    int samplerate;
    int channels;
    int format;
    int sections;
    int seekable;
};

using SndFile = void;

struct SfApi {
    std::mutex mu;
    std::string hint;
    void* lib = nullptr;
    bool use_default = false;
    bool loaded = false;

    SndFile* (*open)(const char*, int, SfInfo*) = nullptr;
#if defined(_WIN32)
    SndFile* (*wchar_open)(const wchar_t*, int, SfInfo*) = nullptr;
#endif
    int (*close)(SndFile*) = nullptr;
    std::int64_t (*readf_double)(SndFile*, double*, std::int64_t) = nullptr;
    std::int64_t (*writef_double)(SndFile*, const double*, std::int64_t) = nullptr;
    std::int64_t (*seek)(SndFile*, std::int64_t, int) = nullptr;
    int (*format_check)(const SfInfo*) = nullptr;
    const char* (*get_string)(SndFile*, int) = nullptr;
    int (*set_string)(SndFile*, int, const char*) = nullptr;
    const char* (*strerror)(SndFile*) = nullptr;
    int (*command)(SndFile*, int, void*, int) = nullptr;

    void* open_lib(const char* p) {
#if defined(_WIN32)
        if (p && *p) {
            int wlen = MultiByteToWideChar(CP_UTF8, 0, p, -1, nullptr, 0);
            if (wlen > 0) {
                std::wstring ws(static_cast<std::size_t>(wlen), L'\0');
                MultiByteToWideChar(CP_UTF8, 0, p, -1, ws.data(), wlen);
                if (HMODULE m = LoadLibraryW(ws.c_str())) return reinterpret_cast<void*>(m);
            }
        }
        const wchar_t* cands[] = {L"sndfile.dll", L"libsndfile-1.dll", L"libsndfile.dll"};
        for (const wchar_t* n : cands) {
            if (HMODULE m = GetModuleHandleW(n)) return reinterpret_cast<void*>(m);
            if (HMODULE m = LoadLibraryW(n)) return reinterpret_cast<void*>(m);
        }
        return nullptr;
#else
        if (p && *p) {
            if (void* h = dlopen(p, RTLD_LAZY | RTLD_LOCAL)) return h;
        }
        const char* cands[] = {
            "libsndfile.so.1",
            "libsndfile.so",
            "libsndfile.1.dylib",
            "libsndfile.dylib",
            "/opt/homebrew/lib/libsndfile.1.dylib",
            "/usr/local/lib/libsndfile.1.dylib",
            "/opt/homebrew/opt/libsndfile/lib/libsndfile.1.dylib",
        };
        for (const char* c : cands) {
            if (void* h = dlopen(c, RTLD_LAZY | RTLD_LOCAL)) return h;
        }
        if (dlsym(RTLD_DEFAULT, "sf_open")) use_default = true;
        return nullptr;
#endif
    }

    void* sym(const char* name) {
#if defined(_WIN32)
        return reinterpret_cast<void*>(GetProcAddress(reinterpret_cast<HMODULE>(lib), name));
#else
        return dlsym(use_default ? RTLD_DEFAULT : lib, name);
#endif
    }

    bool ensure() {
        std::lock_guard<std::mutex> lk(mu);
        if (loaded) return true;
        use_default = false;
        const char* env = std::getenv("ADV_TG_SNDFILE_LIB");
        const char* use_hint = !hint.empty() ? hint.c_str() : env;
        lib = open_lib(use_hint);
        if (!lib && !use_default) {
            set_err("no se pudo cargar 'libsndfile'");
            return false;
        }
        open = reinterpret_cast<decltype(open)>(sym("sf_open"));
#if defined(_WIN32)
        wchar_open = reinterpret_cast<decltype(wchar_open)>(sym("sf_wchar_open"));
#endif
        close = reinterpret_cast<decltype(close)>(sym("sf_close"));
        readf_double = reinterpret_cast<decltype(readf_double)>(sym("sf_readf_double"));
        writef_double = reinterpret_cast<decltype(writef_double)>(sym("sf_writef_double"));
        seek = reinterpret_cast<decltype(seek)>(sym("sf_seek"));
        format_check = reinterpret_cast<decltype(format_check)>(sym("sf_format_check"));
        get_string = reinterpret_cast<decltype(get_string)>(sym("sf_get_string"));
        set_string = reinterpret_cast<decltype(set_string)>(sym("sf_set_string"));
        strerror = reinterpret_cast<decltype(strerror)>(sym("sf_strerror"));
        command = reinterpret_cast<decltype(command)>(sym("sf_command"));
        loaded = open && close && readf_double && writef_double && seek && format_check &&
                 get_string && set_string && strerror && command;
        if (!loaded) set_err("símbolos incompletos en 'libsndfile'");
        return loaded;
    }
};

SfApi& sf_api() {
    static SfApi api;
    return api;
}

SndFile* sf_open_path(const fs::path& p, int mode, SfInfo* info) {
    SfApi& api = sf_api();
    SndFile* f = nullptr;
#if defined(_WIN32)
    if (api.wchar_open) f = api.wchar_open(p.c_str(), mode, info);
#endif
    if (!f) {
        const std::string s = path_u8(p);
        f = api.open(s.c_str(), mode, info);
    }
    if (f) api.command(f, 0x10C0 /* SFC_SET_CLIPPING */, nullptr, 1 /* SF_TRUE */);
    return f;
}

const char* format_name(int fmt) {
    switch (fmt & kSfFormatTypemask) {
        case kSfFormatWav: return "WAV";
        case kSfFormatFlac: return "FLAC";
        case kSfFormatAiff: return "AIFF";
        case kSfFormatOgg: return "OGG";
        case kSfFormatCaf: return "CAF";
        case kSfFormatRf64: return "RF64";
        default: return "OTHER";
    }
}

const char* subtype_name(int fmt) {
    switch (fmt & kSfFormatSubmask) {
        case kSfFormatPcmS8: return "PCM_S8";
        case kSfFormatPcm16: return "PCM_16";
        case kSfFormatPcm24: return "PCM_24";
        case kSfFormatPcm32: return "PCM_32";
        case kSfFormatPcmU8: return "PCM_U8";
        case kSfFormatFloat: return "FLOAT";
        case kSfFormatDouble: return "DOUBLE";
        case kSfFormatVorbis: return "VORBIS";
        case kSfFormatOpus: return "OPUS";
        default: return "OTHER";
    }
}

int parse_subtype_code(const std::string& sub) {
    const std::string u = to_upper_ascii(sub);
    if (u == "PCM_S8") return kSfFormatPcmS8;
    if (u == "PCM_16") return kSfFormatPcm16;
    if (u == "PCM_24") return kSfFormatPcm24;
    if (u == "PCM_32") return kSfFormatPcm32;
    if (u == "PCM_U8") return kSfFormatPcmU8;
    if (u == "FLOAT") return kSfFormatFloat;
    if (u == "DOUBLE") return kSfFormatDouble;
    return 0;
}

bool is_float_subtype(const std::string& sub) {
    const std::string u = to_upper_ascii(sub);
    return u == "FLOAT" || u == "DOUBLE";
}

void fill_info(const SfInfo& raw, advtg_audio_file_info& out) {
    std::memset(&out, 0, sizeof out);
    out.frames = raw.frames;
    out.sample_rate = raw.samplerate;
    out.channels = raw.channels;
    out.duration_s = (raw.samplerate > 0) ? (static_cast<double>(raw.frames) / raw.samplerate) : 0.0;
    std::snprintf(out.format, sizeof out.format, "%s", format_name(raw.format));
    std::snprintf(out.subtype, sizeof out.subtype, "%s", subtype_name(raw.format));
}

bool check_readable(const fs::path& p, int& err_code) {
    std::error_code ec;
    if (!fs::exists(p, ec)) {
        set_err("el archivo de audio no existe: '" + path_u8(p) + "'");
        err_code = ADVTG_ERR_IO;
        return false;
    }
    if (!fs::is_regular_file(p, ec)) {
        set_err("no es un archivo: '" + path_u8(p) + "'");
        err_code = ADVTG_ERR_IO;
        return false;
    }
    return true;
}

bool resolve_write_format(const fs::path& p, const char* subtype_in, std::string& fmt_str,
                          std::string& sub_str, int& sf_format) {
    std::string ext = path_u8(p.extension());
    for (char& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    int fmt_code = 0;
    if (ext == ".wav") {
        fmt_str = "WAV";
        fmt_code = kSfFormatWav;
        sub_str = (subtype_in && *subtype_in) ? to_upper_ascii(subtype_in) : "FLOAT";
    } else if (ext == ".flac") {
        fmt_str = "FLAC";
        fmt_code = kSfFormatFlac;
        sub_str = (subtype_in && *subtype_in) ? to_upper_ascii(subtype_in) : "PCM_24";
    } else {
        set_err("extensión no soportada '" + ext + "' en '" + path_u8(p) +
                "' (usa una de: .wav, .flac)");
        return false;
    }
    const int sub_code = parse_subtype_code(sub_str);
    SfInfo probe{};
    probe.samplerate = 48000;
    probe.channels = 1;
    probe.format = fmt_code | sub_code;
    if (!sub_code || !sf_api().format_check(&probe)) {
        set_err("el formato " + fmt_str + " no admite el subtipo " + sub_str);
        return false;
    }
    sf_format = fmt_code | sub_code;
    return true;
}

int read_info_internal(const fs::path& p, advtg_audio_file_info& out, SfInfo& raw_out) {
    if (!sf_api().ensure()) return ADVTG_ERR_UNAVAILABLE;
    int err_code = ADVTG_OK;
    if (!check_readable(p, err_code)) return err_code;
    std::memset(&raw_out, 0, sizeof raw_out);
    SndFile* f = sf_open_path(p, kSfmRead, &raw_out);
    if (!f) {
        set_err("no se pudo leer '" + path_u8(p) +
                "': formato no soportado o archivo corrupto (" + sf_api().strerror(nullptr) + ")");
        return ADVTG_ERR_FORMAT;
    }
    sf_api().close(f);
    fill_info(raw_out, out);
    return ADVTG_OK;
}

#endif  // ADVTG_WITH_AUDIO_IO

}  // namespace

struct advtg_audio_reader {
    fs::path path;
    void* sf = nullptr;
    advtg_audio_file_info info{};
    std::int64_t blocksize = 4096;
    std::int32_t selected_channel = -1;  // -1 = all channels
    std::int64_t pos = 0;
    std::vector<double> scratch;
};

struct advtg_audio_writer {
    fs::path path;
    fs::path tmp;
    void* sf = nullptr;
    std::string fmt;
    std::string subtype;
    int32_t sample_rate = 48000;
    int32_t channels = 1;
    std::string comment;
    std::vector<TagEntry> tags;
    std::int64_t frames_written = 0;
    std::int64_t clipped_samples = 0;
    std::vector<double> scratch;
};

extern "C" {

ADVTG_C_API int advtg_set_sndfile_library(const char* path) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    return ADVTG_ERR_UNAVAILABLE;
#else
    SfApi& api = sf_api();
    std::lock_guard<std::mutex> lk(api.mu);
    api.hint = path ? path : "";
    api.loaded = false;
    api.lib = nullptr;
    return ADVTG_OK;
#endif
}

ADVTG_C_API const char* advtg_audio_last_error(void) { return g_audio_error.c_str(); }

ADVTG_C_API int advtg_audio_info(const char* path, advtg_audio_file_info* out) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)out;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!path || !out) {
        set_err("argumento inválido");
        return ADVTG_ERR_INVALID;
    }
    SfInfo raw{};
    return read_info_internal(u8_path(path), *out, raw);
#endif
}

ADVTG_C_API int advtg_audio_read_f64(const char* path, int64_t max_bytes, double* out,
                                     size_t cap_samples, advtg_audio_file_info* info_out,
                                     char* comment_buf, int32_t comment_cap) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)max_bytes;
    (void)out;
    (void)cap_samples;
    (void)info_out;
    (void)comment_buf;
    (void)comment_cap;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!path) return ADVTG_ERR_INVALID;
    const fs::path p = u8_path(path);
    advtg_audio_file_info info{};
    SfInfo raw{};
    int rc = read_info_internal(p, info, raw);
    if (rc != ADVTG_OK) return rc;
    const std::int64_t ch = std::max<std::int32_t>(info.channels, 1);
    const std::int64_t needed = info.frames * ch * 8;
    if (max_bytes > 0 && needed > max_bytes) {
        set_err("el archivo '" + path_u8(p.filename()) +
                "' excede el límite de memoria para carga completa");
        return ADVTG_ERR_MEMORY_LIMIT;
    }
    const std::size_t total_samples = static_cast<std::size_t>(info.frames * ch);
    if (info_out) *info_out = info;
    if (!out || cap_samples < total_samples) {
        set_err("búfer insuficiente");
        return ADVTG_ERR_INVALID;
    }
    std::memset(&raw, 0, sizeof raw);
    SndFile* f = sf_open_path(p, kSfmRead, &raw);
    if (!f) {
        set_err("no se pudo leer '" + path_u8(p) + "'");
        return ADVTG_ERR_FORMAT;
    }
    const std::int64_t got = sf_api().readf_double(f, out, info.frames);
    const char* raw_comment = sf_api().get_string(f, kSfStrComment);
    std::string comment = raw_comment ? unescape_comment(raw_comment) : "";
    sf_api().close(f);
    if (got != info.frames) {
        set_err("lectura truncada en '" + path_u8(p) + "'");
        return ADVTG_ERR_FORMAT;
    }
    if (std::strcmp(info.format, "WAV") == 0 && !comment.empty()) {
        std::string rest;
        bool has_rest = false;
        std::vector<std::pair<std::string, std::string>> unused_tags;
        split_wav_comment(comment, rest, has_rest, unused_tags);
        comment = has_rest ? rest : "";
    }
    if (comment_buf && comment_cap > 0) {
        const std::size_t n =
            std::min<std::size_t>(comment.size(), static_cast<std::size_t>(comment_cap - 1));
        if (n) std::memcpy(comment_buf, comment.data(), n);
        comment_buf[n] = '\0';
    }
    return ADVTG_OK;
#endif
}

ADVTG_C_API int advtg_audio_select_channel(const double* interleaved, int64_t frames,
                                           int32_t channels, int32_t mode, double* mono_out) {
    if (!interleaved || !mono_out || frames < 0 || channels < 1) {
        set_err("argumento inválido en select_channel");
        return ADVTG_ERR_INVALID;
    }
    int32_t idx = 0;
    if (mode == -1) {
        if (channels > 1) {
            std::vector<double> energies(static_cast<std::size_t>(channels), 0.0);
            for (int64_t i = 0; i < frames; ++i) {
                for (int32_t c = 0; c < channels; ++c) {
                    const double v = interleaved[i * channels + c];
                    energies[static_cast<std::size_t>(c)] += v * v;
                }
            }
            for (int32_t c = 1; c < channels; ++c) {
                if (energies[static_cast<std::size_t>(c)] > energies[static_cast<std::size_t>(idx)])
                    idx = c;
            }
        }
    } else {
        if (mode < 0 || mode >= channels) {
            set_err("canal " + std::to_string(mode) + " fuera de rango");
            return ADVTG_ERR_INVALID;
        }
        idx = mode;
    }
    if (channels == 1) {
        if (frames > 0)
            std::memcpy(mono_out, interleaved, static_cast<std::size_t>(frames) * sizeof(double));
    } else {
        for (int64_t i = 0; i < frames; ++i) mono_out[i] = interleaved[i * channels + idx];
    }
    return idx;
}

ADVTG_C_API int advtg_audio_read_mono(const char* path, int32_t channel_mode, int64_t max_bytes,
                                      double* out, size_t cap_frames,
                                      advtg_audio_file_info* info_out) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)channel_mode;
    (void)max_bytes;
    (void)out;
    (void)cap_frames;
    (void)info_out;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!path) return ADVTG_ERR_INVALID;
    advtg_audio_file_info info{};
    SfInfo raw{};
    int rc = read_info_internal(u8_path(path), info, raw);
    if (rc != ADVTG_OK) return rc;
    if (info_out) *info_out = info;
    if (!out || cap_frames < static_cast<size_t>(info.frames)) {
        set_err("búfer insuficiente");
        return ADVTG_ERR_INVALID;
    }
    if (info.channels <= 1) {
        if (channel_mode > 0) {
            set_err("canal fuera de rango (la señal es mono)");
            return ADVTG_ERR_INVALID;
        }
        return advtg_audio_read_f64(path, max_bytes, out, cap_frames, nullptr, nullptr, 0);
    }
    const std::size_t total = static_cast<std::size_t>(info.frames * info.channels);
    std::vector<double> tmp(total);
    rc = advtg_audio_read_f64(path, max_bytes, tmp.data(), total, nullptr, nullptr, 0);
    if (rc != ADVTG_OK) return rc;
    rc = advtg_audio_select_channel(tmp.data(), info.frames, info.channels, channel_mode, out);
    return rc >= 0 ? ADVTG_OK : rc;
#endif
}

ADVTG_C_API int advtg_audio_read_tags(const char* path, advtg_tag_cb cb, void* user) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)cb;
    (void)user;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!path || !cb) return ADVTG_ERR_INVALID;
    const fs::path p = u8_path(path);
    advtg_audio_file_info info{};
    SfInfo raw{};
    int rc = read_info_internal(p, info, raw);
    if (rc != ADVTG_OK) return rc;
    if (std::strcmp(info.format, "FLAC") == 0) {
        std::vector<std::pair<std::string, std::string>> tags;
        std::string err;
        if (!flac_read_tags_file(p, tags, err)) {
            set_err("no se pudo leer '" + path_u8(p) + "': " + err);
            return ADVTG_ERR_FORMAT;
        }
        for (const auto& kv : tags) cb(kv.first.c_str(), kv.second.c_str(), user);
        return ADVTG_OK;
    }
    if (std::strcmp(info.format, "WAV") == 0) {
        std::memset(&raw, 0, sizeof raw);
        SndFile* f = sf_open_path(p, kSfmRead, &raw);
        if (!f) {
            set_err("no se pudo leer '" + path_u8(p) + "'");
            return ADVTG_ERR_FORMAT;
        }
        const char* raw_comment = sf_api().get_string(f, kSfStrComment);
        const std::string text = raw_comment ? unescape_comment(raw_comment) : "";
        sf_api().close(f);
        std::string rest;
        bool has_rest = false;
        std::vector<std::pair<std::string, std::string>> tags;
        split_wav_comment(text, rest, has_rest, tags);
        for (const auto& kv : tags) cb(kv.first.c_str(), kv.second.c_str(), user);
        return ADVTG_OK;
    }
    return ADVTG_OK;
#endif
}

ADVTG_C_API int advtg_audio_update_tags(const char* path, const advtg_audio_tag* tags,
                                        size_t n_tags) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)tags;
    (void)n_tags;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!path) return ADVTG_ERR_INVALID;
    std::vector<TagEntry> checked;
    if (!check_tags(tags, n_tags, checked)) return ADVTG_ERR_INVALID;
    const fs::path p = u8_path(path);
    advtg_audio_file_info info{};
    SfInfo raw{};
    int rc = read_info_internal(p, info, raw);
    if (rc != ADVTG_OK) return rc;
    if (std::strcmp(info.format, "FLAC") == 0) {
        std::string err;
        if (!flac_set_tags_file(p, checked, err)) {
            set_err("no se pudieron escribir las etiquetas de '" + path_u8(p) + "': " + err);
            return ADVTG_ERR_IO;
        }
        return ADVTG_OK;
    }
    if (std::strcmp(info.format, "WAV") == 0) {
        const std::size_t total =
            static_cast<std::size_t>(info.frames * std::max<int32_t>(info.channels, 1));
        std::vector<double> samples(total);
        std::memset(&raw, 0, sizeof raw);
        SndFile* f = sf_open_path(p, kSfmRead, &raw);
        if (!f) {
            set_err("no se pudo leer '" + path_u8(p) + "'");
            return ADVTG_ERR_IO;
        }
        const std::int64_t got = sf_api().readf_double(f, samples.data(), info.frames);
        const char* raw_comment = sf_api().get_string(f, kSfStrComment);
        const std::string text = raw_comment ? unescape_comment(raw_comment) : "";
        sf_api().close(f);
        if (got != info.frames) {
            set_err("lectura truncada de '" + path_u8(p) + "'");
            return ADVTG_ERR_IO;
        }
        std::string comment;
        bool has_comment = false;
        std::vector<std::pair<std::string, std::string>> old_tags;
        split_wav_comment(text, comment, has_comment, old_tags);
        std::vector<TagEntry> merged;
        for (const auto& kv : old_tags) merged.push_back({kv.first, true, kv.second});
        for (const auto& t : checked) {
            bool found = false;
            for (auto& m : merged) {
                if (m.key == t.key) {
                    m.has_value = t.has_value;
                    m.value = t.value;
                    found = true;
                    break;
                }
            }
            if (!found) merged.push_back(t);
        }
        std::vector<advtg_audio_tag> c_tags;
        for (const auto& m : merged) {
            if (m.has_value) c_tags.push_back({m.key.c_str(), m.value.c_str()});
        }
        return advtg_audio_write_f64(path, samples.data(), info.frames, info.channels,
                                     info.sample_rate, info.subtype,
                                     has_comment ? comment.c_str() : nullptr,
                                     c_tags.empty() ? nullptr : c_tags.data(), c_tags.size(),
                                     nullptr);
    }
    set_err("no se pueden etiquetar archivos " + std::string(info.format));
    return ADVTG_ERR_FORMAT;
#endif
}

ADVTG_C_API int advtg_audio_write_f64(const char* path, const double* samples, int64_t frames,
                                      int32_t channels, int32_t sample_rate,
                                      const char* subtype, const char* comment,
                                      const advtg_audio_tag* tags, size_t n_tags,
                                      int64_t* clipped_out) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)samples;
    (void)frames;
    (void)channels;
    (void)sample_rate;
    (void)subtype;
    (void)comment;
    (void)tags;
    (void)n_tags;
    (void)clipped_out;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (clipped_out) *clipped_out = 0;
    if (!path || (!samples && frames > 0) || frames < 0 || channels <= 0) {
        set_err("argumento inválido al escribir audio");
        return ADVTG_ERR_INVALID;
    }
    if (!sf_api().ensure()) return ADVTG_ERR_UNAVAILABLE;
    const fs::path p = u8_path(path);
    if (sample_rate <= 0) {
        set_err("tasa de muestreo no válida (" + std::to_string(sample_rate) + ")");
        return ADVTG_ERR_INVALID;
    }
    std::string fmt_str, sub_str;
    int sf_fmt = 0;
    if (!resolve_write_format(p, subtype, fmt_str, sub_str, sf_fmt)) return ADVTG_ERR_FORMAT;
    std::vector<TagEntry> checked;
    if (!check_tags(tags, n_tags, checked)) return ADVTG_ERR_INVALID;

    const std::size_t total = static_cast<std::size_t>(frames * channels);
    for (std::size_t i = 0; i < total; ++i) {
        if (!std::isfinite(samples[i])) {
            set_err("las muestras para '" + path_u8(p) + "' contienen NaN o infinitos");
            return ADVTG_ERR_INVALID;
        }
    }
    std::vector<double> clipped_buf;
    const double* write_ptr = samples;
    std::int64_t clipped = 0;
    if (!is_float_subtype(sub_str) && total > 0) {
        for (std::size_t i = 0; i < total; ++i) {
            if (std::fabs(samples[i]) > 1.0) ++clipped;
        }
        if (clipped > 0) {
            clipped_buf.resize(total);
            for (std::size_t i = 0; i < total; ++i) {
                clipped_buf[i] = std::clamp(samples[i], -1.0, 1.0);
            }
            write_ptr = clipped_buf.data();
        }
    }
    if (clipped_out) *clipped_out = clipped;

    fs::path tmp = make_temp_path(p);
    auto cleanup = [&]() {
        std::error_code ec;
        fs::remove(tmp, ec);
    };
    SfInfo raw{};
    raw.samplerate = sample_rate;
    raw.channels = channels;
    raw.format = sf_fmt;
    SndFile* f = sf_open_path(tmp, kSfmWrite, &raw);
    if (!f) {
        set_err("no se pudo escribir '" + path_u8(p) + "': " + sf_api().strerror(nullptr));
        cleanup();
        return ADVTG_ERR_IO;
    }
    const std::string ctext =
        (fmt_str == "WAV") ? join_wav_comment(comment, checked) : (comment ? comment : "");
    if (!ctext.empty()) {
        const std::string esc = escape_comment(ctext);
        sf_api().set_string(f, kSfStrComment, esc.c_str());
    }
    const std::int64_t wrote =
        (frames > 0) ? sf_api().writef_double(f, write_ptr, frames) : 0;
    const int close_rc = sf_api().close(f);
    if (wrote != frames || close_rc != 0) {
        set_err("no se pudo escribir '" + path_u8(p) + "'");
        cleanup();
        return ADVTG_ERR_IO;
    }
    if (fmt_str == "FLAC" && !checked.empty()) {
        std::string err;
        if (!flac_set_tags_file(tmp, checked, err)) {
            set_err("no se pudo escribir '" + path_u8(p) + "': " + err);
            cleanup();
            return ADVTG_ERR_IO;
        }
    }
    std::string err;
    if (!atomic_replace(tmp, p, err)) {
        set_err("no se pudo escribir '" + path_u8(p) + "': " + err);
        cleanup();
        return ADVTG_ERR_IO;
    }
    return ADVTG_OK;
#endif
}

ADVTG_C_API advtg_audio_reader* advtg_audio_reader_open(const char* path, int64_t blocksize,
                                                        int32_t channel_mode) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)blocksize;
    (void)channel_mode;
    return nullptr;
#else
    if (!path || blocksize <= 0) {
        set_err("blocksize debe ser positivo");
        return nullptr;
    }
    if (!sf_api().ensure()) return nullptr;
    const fs::path p = u8_path(path);
    int err_code = 0;
    if (!check_readable(p, err_code)) return nullptr;
    SfInfo raw{};
    SndFile* f = sf_open_path(p, kSfmRead, &raw);
    if (!f) {
        set_err("no se pudo leer '" + path_u8(p) + "': " + sf_api().strerror(nullptr));
        return nullptr;
    }
    auto r = std::make_unique<advtg_audio_reader>();
    r->path = p;
    r->sf = f;
    r->blocksize = blocksize;
    fill_info(raw, r->info);
    if (channel_mode == -2) {
        r->selected_channel = -1;  // all channels
    } else if (channel_mode == -1) {
        if (raw.channels <= 1) {
            r->selected_channel = 0;
        } else {
            std::vector<double> energies(static_cast<std::size_t>(raw.channels), 0.0);
            std::vector<double> buf(static_cast<std::size_t>(blocksize * raw.channels));
            while (true) {
                const std::int64_t got = sf_api().readf_double(f, buf.data(), blocksize);
                if (got <= 0) break;
                for (std::int64_t i = 0; i < got; ++i) {
                    for (int c = 0; c < raw.channels; ++c) {
                        const double v = buf[static_cast<std::size_t>(i * raw.channels + c)];
                        energies[static_cast<std::size_t>(c)] += v * v;
                    }
                }
            }
            sf_api().seek(f, 0, kSeekSet);
            int best = 0;
            for (int c = 1; c < raw.channels; ++c) {
                if (energies[static_cast<std::size_t>(c)] > energies[static_cast<std::size_t>(best)])
                    best = c;
            }
            r->selected_channel = best;
        }
    } else {
        if (channel_mode < 0 || channel_mode >= raw.channels) {
            sf_api().close(f);
            set_err("canal fuera de rango");
            return nullptr;
        }
        r->selected_channel = channel_mode;
    }
    return r.release();
#endif
}

ADVTG_C_API int advtg_audio_reader_info(const advtg_audio_reader* r, advtg_audio_file_info* out,
                                        int32_t* selected_channel_out) {
    if (!r || !out) return ADVTG_ERR_INVALID;
    *out = r->info;
    if (selected_channel_out) *selected_channel_out = r->selected_channel;
    return ADVTG_OK;
}

ADVTG_C_API int64_t advtg_audio_reader_read_block(advtg_audio_reader* r, double* out,
                                                  size_t cap_samples, int64_t* start_index_out) {
#if !ADVTG_WITH_AUDIO_IO
    (void)r;
    (void)out;
    (void)cap_samples;
    (void)start_index_out;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (!r || !r->sf || !out) return ADVTG_ERR_INVALID;
    const int32_t ch = std::max<int32_t>(r->info.channels, 1);
    const std::size_t need =
        static_cast<std::size_t>(r->blocksize) * (r->selected_channel < 0 ? ch : 1);
    if (cap_samples < need) return ADVTG_ERR_INVALID;
    if (start_index_out) *start_index_out = r->pos;
    if (r->selected_channel < 0 || ch == 1) {
        const std::int64_t got = sf_api().readf_double(r->sf, out, r->blocksize);
        if (got < 0) return ADVTG_ERR_IO;
        r->pos += got;
        return got;
    }
    r->scratch.resize(static_cast<std::size_t>(r->blocksize) * static_cast<std::size_t>(ch));
    const std::int64_t got = sf_api().readf_double(r->sf, r->scratch.data(), r->blocksize);
    if (got < 0) return ADVTG_ERR_IO;
    for (std::int64_t i = 0; i < got; ++i) {
        out[i] = r->scratch[static_cast<std::size_t>(i * ch + r->selected_channel)];
    }
    r->pos += got;
    return got;
#endif
}

ADVTG_C_API void advtg_audio_reader_close(advtg_audio_reader* r) {
#if ADVTG_WITH_AUDIO_IO
    if (r && r->sf) sf_api().close(r->sf);
#endif
    delete r;
}

ADVTG_C_API advtg_audio_writer* advtg_audio_writer_open(const char* path, int32_t sample_rate,
                                                        int32_t channels, const char* subtype,
                                                        const char* comment,
                                                        const advtg_audio_tag* tags,
                                                        size_t n_tags) {
#if !ADVTG_WITH_AUDIO_IO
    (void)path;
    (void)sample_rate;
    (void)channels;
    (void)subtype;
    (void)comment;
    (void)tags;
    (void)n_tags;
    return nullptr;
#else
    if (!path || sample_rate <= 0 || channels <= 0) {
        set_err("parámetros inválidos en AudioWriter");
        return nullptr;
    }
    if (!sf_api().ensure()) return nullptr;
    const fs::path p = u8_path(path);
    std::string fmt_str, sub_str;
    int sf_fmt = 0;
    if (!resolve_write_format(p, subtype, fmt_str, sub_str, sf_fmt)) return nullptr;
    std::vector<TagEntry> checked;
    if (!check_tags(tags, n_tags, checked)) return nullptr;

    auto w = std::make_unique<advtg_audio_writer>();
    w->path = p;
    w->tmp = make_temp_path(p);
    w->fmt = fmt_str;
    w->subtype = sub_str;
    w->sample_rate = sample_rate;
    w->channels = channels;
    w->comment = comment ? comment : "";
    w->tags = std::move(checked);

    SfInfo raw{};
    raw.samplerate = sample_rate;
    raw.channels = channels;
    raw.format = sf_fmt;
    SndFile* f = sf_open_path(w->tmp, kSfmWrite, &raw);
    if (!f) {
        set_err("no se pudo crear '" + path_u8(p) + "': " + sf_api().strerror(nullptr));
        std::error_code ec;
        fs::remove(w->tmp, ec);
        return nullptr;
    }
    const std::string ctext =
        (fmt_str == "WAV") ? join_wav_comment(comment, w->tags) : (comment ? comment : "");
    if (!ctext.empty()) {
        const std::string esc = escape_comment(ctext);
        sf_api().set_string(f, kSfStrComment, esc.c_str());
    }
    w->sf = f;
    return w.release();
#endif
}

ADVTG_C_API int advtg_audio_writer_write(advtg_audio_writer* w, const double* samples,
                                         int64_t frames, int32_t channels,
                                         int64_t* clipped_now_out) {
#if !ADVTG_WITH_AUDIO_IO
    (void)w;
    (void)samples;
    (void)frames;
    (void)channels;
    (void)clipped_now_out;
    return ADVTG_ERR_UNAVAILABLE;
#else
    if (clipped_now_out) *clipped_now_out = 0;
    if (!w || !w->sf || (!samples && frames > 0) || frames < 0) {
        set_err("AudioWriter no está abierto o bloque inválido");
        return ADVTG_ERR_INVALID;
    }
    if (channels != w->channels) {
        set_err("bloque con " + std::to_string(channels) + " canales; se esperaban " +
                std::to_string(w->channels));
        return ADVTG_ERR_INVALID;
    }
    const std::size_t total = static_cast<std::size_t>(frames * channels);
    for (std::size_t i = 0; i < total; ++i) {
        if (!std::isfinite(samples[i])) {
            set_err("las muestras contienen NaN o infinitos");
            return ADVTG_ERR_INVALID;
        }
    }
    const double* write_ptr = samples;
    std::int64_t clipped = 0;
    if (!is_float_subtype(w->subtype) && total > 0) {
        for (std::size_t i = 0; i < total; ++i) {
            if (std::fabs(samples[i]) > 1.0) ++clipped;
        }
        if (clipped > 0) {
            w->scratch.resize(total);
            for (std::size_t i = 0; i < total; ++i) {
                w->scratch[i] = std::clamp(samples[i], -1.0, 1.0);
            }
            write_ptr = w->scratch.data();
        }
    }
    if (clipped > 0) {
        w->clipped_samples += clipped;
        if (clipped_now_out) *clipped_now_out = clipped;
    }
    if (frames > 0) {
        const std::int64_t wrote = sf_api().writef_double(w->sf, write_ptr, frames);
        if (wrote != frames) {
            set_err("no se pudo escribir bloque en '" + path_u8(w->path) + "'");
            return ADVTG_ERR_IO;
        }
    }
    w->frames_written += frames;
    return ADVTG_OK;
#endif
}

ADVTG_C_API int advtg_audio_writer_set_tag(advtg_audio_writer* w, const char* key,
                                           const char* value) {
    if (!w || !key) return ADVTG_ERR_INVALID;
    advtg_audio_tag tag{key, value};
    std::vector<TagEntry> checked;
    if (!check_tags(&tag, 1, checked)) return ADVTG_ERR_INVALID;
    const auto& entry = checked[0];
    for (auto& existing : w->tags) {
        if (existing.key == entry.key) {
            existing.has_value = entry.has_value;
            existing.value = entry.value;
            return ADVTG_OK;
        }
    }
    w->tags.push_back(entry);
    return ADVTG_OK;
}

ADVTG_C_API int advtg_audio_writer_close(advtg_audio_writer* w, int discard,
                                         int64_t* frames_written_out,
                                         int64_t* clipped_total_out) {
    if (!w) return ADVTG_OK;
    if (frames_written_out) *frames_written_out = w->frames_written;
    if (clipped_total_out) *clipped_total_out = w->clipped_samples;
#if !ADVTG_WITH_AUDIO_IO
    delete w;
    (void)discard;
    return ADVTG_ERR_UNAVAILABLE;
#else
    auto cleanup = [&]() {
        std::error_code ec;
        if (!w->tmp.empty()) fs::remove(w->tmp, ec);
    };
    int rc = ADVTG_OK;
    if (w->sf) {
        if (sf_api().close(w->sf) != 0) rc = ADVTG_ERR_IO;
        w->sf = nullptr;
    }
    if (discard || rc != ADVTG_OK) {
        cleanup();
        delete w;
        return rc;
    }
    if (w->fmt == "FLAC" && !w->tags.empty()) {
        std::string err;
        if (!flac_set_tags_file(w->tmp, w->tags, err)) {
            set_err("no se pudo escribir '" + path_u8(w->path) + "': " + err);
            cleanup();
            delete w;
            return ADVTG_ERR_IO;
        }
    }
    std::string err;
    if (!atomic_replace(w->tmp, w->path, err)) {
        set_err("no se pudo escribir '" + path_u8(w->path) + "': " + err);
        cleanup();
        delete w;
        return ADVTG_ERR_IO;
    }
    delete w;
    return ADVTG_OK;
#endif
}

}  // extern "C"
