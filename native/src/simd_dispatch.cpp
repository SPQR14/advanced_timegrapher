// CPU feature detection (CPUID + XGETBV) and selection of the SIMD kernel level.
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <string>

#include "advtg_simd.hpp"

#if defined(__x86_64__) || defined(_M_X64)
#  define ADVTG_X86 1
#  if defined(_MSC_VER)
#    include <immintrin.h>
#    include <intrin.h>
#  else
#    include <cpuid.h>
#  endif
#endif

namespace advtg {
namespace {

struct CpuInfo {
    bool sse42 = false, popcnt = false, fma = false, avx2 = false, bmi2 = false;
    bool avx512 = false;  // F + DQ + BW + VL and the OS saves the ZMM / opmask state
    bool neon = false;
};

#ifdef ADVTG_X86
void cpuid(unsigned leaf, unsigned sub, unsigned& a, unsigned& b, unsigned& c, unsigned& d) {
#  if defined(_MSC_VER)
    int r[4];
    __cpuidex(r, static_cast<int>(leaf), static_cast<int>(sub));
    a = r[0]; b = r[1]; c = r[2]; d = r[3];
#  else
    __cpuid_count(leaf, sub, a, b, c, d);
#  endif
}

unsigned long long xgetbv0() {
#  if defined(_MSC_VER)
    return _xgetbv(0);
#  else
    unsigned eax, edx;
    __asm__ volatile("xgetbv" : "=a"(eax), "=d"(edx) : "c"(0));
    return (static_cast<unsigned long long>(edx) << 32) | eax;
#  endif
}
#endif

CpuInfo detect() {
    CpuInfo ci;
#ifdef ADVTG_X86
    unsigned a = 0, b = 0, c = 0, d = 0;
    cpuid(0, 0, a, b, c, d);
    const unsigned max_leaf = a;
    if (max_leaf < 1) return ci;
    cpuid(1, 0, a, b, c, d);
    ci.sse42 = (c >> 20) & 1U;
    ci.popcnt = (c >> 23) & 1U;
    ci.fma = (c >> 12) & 1U;
    const bool osxsave = (c >> 27) & 1U;
    const bool avx = (c >> 28) & 1U;
    unsigned long long xcr0 = osxsave ? xgetbv0() : 0ULL;
    const bool ymm_ok = avx && ((xcr0 & 0x6ULL) == 0x6ULL);
    const bool zmm_ok = ymm_ok && ((xcr0 & 0xE6ULL) == 0xE6ULL);
    if (max_leaf >= 7) {
        cpuid(7, 0, a, b, c, d);
        ci.avx2 = ymm_ok && ((b >> 5) & 1U);
        ci.bmi2 = (b >> 8) & 1U;
        const bool f = (b >> 16) & 1U, dq = (b >> 17) & 1U, bw = (b >> 30) & 1U, vl = (b >> 31) & 1U;
        ci.avx512 = zmm_ok && f && dq && bw && vl;
    }
#elif defined(__aarch64__) || defined(_M_ARM64)
    ci.neon = true;  // Advanced SIMD is mandatory in AArch64
#endif
    return ci;
}

const CpuInfo& cpu() {
    static const CpuInfo ci = detect();
    return ci;
}

const Kernels* by_name_impl(const char* name) {
    if (!name) return nullptr;
    const CpuInfo& ci = cpu();
    if (!std::strcmp(name, "generic")) return &kernels_generic();
#if defined(ADVTG_HAVE_SSE42)
    if (!std::strcmp(name, "sse42") && ci.sse42 && ci.popcnt) return &kernels_sse42();
#endif
#if defined(ADVTG_HAVE_AVX2)
    if (!std::strcmp(name, "avx2") && ci.avx2 && ci.fma) return &kernels_avx2();
#endif
#if defined(ADVTG_HAVE_AVX512)
    if (!std::strcmp(name, "avx512") && ci.avx512 && ci.fma) return &kernels_avx512();
#endif
#if defined(ADVTG_HAVE_NEON)
    if (!std::strcmp(name, "neon") && ci.neon) return &kernels_neon();
#endif
    (void)ci;
    return nullptr;
}

const Kernels& best() {
    static const char* const order[] = {"avx512", "avx2", "sse42", "neon", "generic"};
    for (const char* n : order)
        if (const Kernels* k = by_name_impl(n)) return *k;
    return kernels_generic();
}

std::atomic<const Kernels*>& active() {
    static std::atomic<const Kernels*> a{[] {
        const char* env = std::getenv("ADVTG_SIMD");
        if (env && *env && std::strcmp(env, "auto"))
            if (const Kernels* k = by_name_impl(env)) return k;
        return &best();
    }()};
    return a;
}

}  // namespace

const Kernels& kernels() { return *active().load(std::memory_order_acquire); }

const Kernels* kernels_by_name(const char* name) { return by_name_impl(name); }

bool set_simd_level(const char* name) {
    if (!name || !std::strcmp(name, "auto")) {
        active().store(&best(), std::memory_order_release);
        return true;
    }
    const Kernels* k = by_name_impl(name);
    if (!k) return false;
    active().store(k, std::memory_order_release);
    return true;
}

const char* simd_level_name() { return kernels().name; }

std::string cpu_feature_string() {
    const CpuInfo& ci = cpu();
    std::string s;
#ifdef ADVTG_X86
    s = "x86_64";
    if (ci.sse42) s += " SSE4.2";
    if (ci.avx2) s += " AVX2";
    if (ci.fma) s += " FMA";
    if (ci.bmi2) s += " BMI2";
    if (ci.avx512) s += " AVX512F+DQ+BW+VL";
#elif defined(__aarch64__) || defined(_M_ARM64)
    s = "arm64";
    if (ci.neon) s += " NEON";
#else
    s = "unknown";
#endif
    return s;
}

}  // namespace advtg
