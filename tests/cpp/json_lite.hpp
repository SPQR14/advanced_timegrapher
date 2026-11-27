// Minimal JSON reader for the golden files (objects, arrays, strings, numbers, true/false/null).
// Numbers keep whether they were written as integers ("245") or floats ("245.0"), as Python's
// json module writes them, so integer fields are compared exactly.
#pragma once

#include <cctype>
#include <cstdlib>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace jsonl {

struct Value {
    enum Kind { Null, Bool, Int, Float, String, Array, Object } kind = Null;
    bool b = false;
    long long i = 0;
    double f = 0.0;
    std::string s;
    std::vector<Value> a;
    std::map<std::string, Value> o;

    double number() const { return kind == Int ? static_cast<double>(i) : f; }
};

class Parser {
public:
    explicit Parser(const std::string& text) : t_(text) {}
    Value parse() {
        Value v = value();
        ws();
        if (p_ != t_.size()) fail("trailing characters");
        return v;
    }

private:
    const std::string& t_;
    std::size_t p_ = 0;

    [[noreturn]] void fail(const char* what) const {
        throw std::runtime_error(std::string("json: ") + what + " at " + std::to_string(p_));
    }
    void ws() {
        while (p_ < t_.size() && std::isspace(static_cast<unsigned char>(t_[p_]))) ++p_;
    }
    bool lit(const char* w) {
        const std::size_t n = std::char_traits<char>::length(w);
        if (t_.compare(p_, n, w) == 0) {
            p_ += n;
            return true;
        }
        return false;
    }
    std::string str() {
        if (t_[p_] != '"') fail("expected string");
        ++p_;
        std::string out;
        while (p_ < t_.size() && t_[p_] != '"') {
            char c = t_[p_++];
            if (c == '\\') {
                const char e = t_[p_++];
                if (e == 'u') {  // \uXXXX (BMP only; enough for the golden files)
                    const unsigned cp = static_cast<unsigned>(std::stoul(t_.substr(p_, 4), nullptr, 16));
                    p_ += 4;
                    if (cp < 0x80) {
                        out += static_cast<char>(cp);
                    } else if (cp < 0x800) {
                        out += static_cast<char>(0xC0 | (cp >> 6));
                        out += static_cast<char>(0x80 | (cp & 0x3F));
                    } else {
                        out += static_cast<char>(0xE0 | (cp >> 12));
                        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                        out += static_cast<char>(0x80 | (cp & 0x3F));
                    }
                    continue;
                }
                c = e == 'n' ? '\n' : e == 't' ? '\t' : e;
            }
            out += c;
        }
        if (p_ >= t_.size()) fail("unterminated string");
        ++p_;
        return out;
    }
    Value value() {
        ws();
        if (p_ >= t_.size()) fail("unexpected end");
        Value v;
        const char c = t_[p_];
        if (c == '{') {
            v.kind = Value::Object;
            ++p_;
            ws();
            if (t_[p_] == '}') {
                ++p_;
                return v;
            }
            while (true) {
                ws();
                std::string k = str();
                ws();
                if (t_[p_++] != ':') fail("expected ':'");
                v.o[k] = value();
                ws();
                if (t_[p_] == ',') {
                    ++p_;
                    continue;
                }
                if (t_[p_++] != '}') fail("expected '}'");
                return v;
            }
        }
        if (c == '[') {
            v.kind = Value::Array;
            ++p_;
            ws();
            if (t_[p_] == ']') {
                ++p_;
                return v;
            }
            while (true) {
                v.a.push_back(value());
                ws();
                if (t_[p_] == ',') {
                    ++p_;
                    continue;
                }
                if (t_[p_++] != ']') fail("expected ']'");
                return v;
            }
        }
        if (c == '"') {
            v.kind = Value::String;
            v.s = str();
            return v;
        }
        if (lit("null")) return v;
        if (lit("true")) {
            v.kind = Value::Bool;
            v.b = true;
            return v;
        }
        if (lit("false")) {
            v.kind = Value::Bool;
            return v;
        }
        const std::size_t start = p_;
        while (p_ < t_.size() && (std::isdigit(static_cast<unsigned char>(t_[p_])) ||
                                  t_[p_] == '-' || t_[p_] == '+' || t_[p_] == '.' ||
                                  t_[p_] == 'e' || t_[p_] == 'E'))
            ++p_;
        const std::string num = t_.substr(start, p_ - start);
        if (num.empty()) fail("unexpected character");
        if (num.find_first_of(".eE") == std::string::npos) {
            v.kind = Value::Int;
            v.i = std::stoll(num);
        } else {
            v.kind = Value::Float;
            v.f = std::strtod(num.c_str(), nullptr);  // shortest repr -> the same double
        }
        return v;
    }
};

inline Value parse(const std::string& text) { return Parser(text).parse(); }

}  // namespace jsonl
