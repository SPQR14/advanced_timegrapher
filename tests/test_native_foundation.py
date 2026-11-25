"""Foundation of the native engine: SIMD levels, exact median and the bit bijection."""

from __future__ import annotations

import ctypes
import math

import numpy as np
import pytest

from adv_tg import native_bridge as nb

pytestmark = pytest.mark.skipif(not nb.is_native_available(), reason="native engine not built")

LEVELS = ("generic", "sse42", "avx2", "avx512", "neon")


@pytest.fixture(autouse=True)
def _restore_simd():
    yield
    nb.lib().advtg_set_simd(b"auto")


def _levels() -> list[str]:
    cdll = nb.lib()  # also evaluated at collection (parametrize): None without the engine
    if cdll is None:
        return []
    return [lv for lv in LEVELS if cdll.advtg_simd_available(lv.encode())]


def _set(level: str) -> None:
    assert nb.lib().advtg_set_simd(level.encode()) == 1


def test_info_and_levels() -> None:
    info = nb.native_arch_info()
    assert info.startswith("C++20 v") and nb.simd_level() in LEVELS
    assert "generic" in _levels()


@pytest.mark.parametrize("n", [0, 1, 2, 3, 7, 32, 33, 100, 1023, 1024, 1025, 5000, 48000, 48001])
def test_exact_median_matches_numpy(n: int) -> None:
    rng = np.random.default_rng(n)
    for gen in (
        rng.standard_normal(n),
        rng.standard_normal(n) * 1e-9,
        np.abs(rng.standard_normal(n)) ** 3,
        np.round(rng.standard_normal(n), 1),  # many ties
        np.full(n, -0.0) if n else np.zeros(0),
    ):
        v = np.ascontiguousarray(gen, dtype=np.float64).copy()
        got = nb.lib().advtg_median_f64(nb.ptr(v), v.size)
        if n == 0:
            assert math.isnan(got)
        else:
            assert got == float(np.median(gen))  # bit for bit


def test_median_with_negatives_infinities_and_zeros() -> None:
    v = np.array([-np.inf, -3.5, -0.0, 0.0, 1e-300, 7.0, np.inf, -1e300] * 200, dtype=np.float64)
    ref = float(np.median(v))
    assert nb.lib().advtg_median_f64(nb.ptr(v.copy()), v.size) == ref


def test_f64_key_is_an_order_preserving_bijection() -> None:
    xs = np.array(
        [-np.inf, -1e300, -2.5, -1e-300, -5e-324, 0.0, 5e-324, 1e-300, 2.5, 1e300, np.inf]
    )
    keys = [nb.lib().advtg_f64_key(float(x)) for x in xs]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert nb.lib().advtg_f64_key(-0.0) == nb.lib().advtg_f64_key(0.0)
    for x, k in zip(xs, keys, strict=True):
        assert nb.lib().advtg_key_f64(k) == x
    rng = np.random.default_rng(0)
    r = rng.standard_normal(500) * 10.0 ** rng.integers(-300, 300, 500)
    k = np.array([nb.lib().advtg_f64_key(float(x)) for x in r], dtype=np.uint64)
    assert np.array_equal(np.argsort(k, kind="stable"), np.argsort(r, kind="stable"))


@pytest.mark.parametrize("level", _levels() or ["generic"])
@pytest.mark.parametrize("n", [0, 1, 5, 64, 1001])
def test_dot(level: str, n: int) -> None:
    _set(level)
    rng = np.random.default_rng(n)
    a, b = rng.standard_normal(n), rng.standard_normal(n)
    got = nb.lib().advtg_dot_f64(nb.ptr(a), nb.ptr(b), n)
    assert got == pytest.approx(float(np.dot(a, b)), rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("level", _levels() or ["generic"])
@pytest.mark.parametrize("m", [1, 2, 3, 6, 23, 24, 25, 64, 65])
def test_median_segments(level: str, m: int) -> None:
    _set(level)
    rng = np.random.default_rng(m)
    length, stride = 1003, 1024
    base = rng.standard_normal((m, stride))
    out = np.empty(length)
    nb.lib().advtg_median_segments_f64(nb.ptr(base), stride, m, length, nb.ptr(out))
    np.testing.assert_array_equal(out, np.median(base[:, :length], axis=0))  # bit for bit


@pytest.mark.parametrize("level", _levels() or ["generic"])
def test_hilbert_fold_matches_convolution(level: str) -> None:
    _set(level)
    fs = 48000
    half = 2 * math.ceil(1.5 * fs / 1000.0) + 1
    half = (half - 1) // 2
    idx = np.arange(-half, half + 1)
    hk = np.zeros(idx.size)
    odd = idx % 2 != 0
    hk[odd] = 2.0 / (np.pi * idx[odd])
    hk *= np.blackman(idx.size)
    d = np.arange(1, half + 1, 2, dtype=np.int32)
    c = np.ascontiguousarray(hk[half + d])
    n = 4999
    y = np.random.default_rng(1).standard_normal(n)
    yp = np.concatenate((np.zeros(half), y, np.zeros(half)))
    out = np.empty(n)
    nb.lib().advtg_hilbert_fold_f64(nb.ptr(yp), n, half, nb.ptr(c), nb.ptr(d), d.size, nb.ptr(out))
    ref = np.convolve(y, hk, mode="full")[half : half + n]
    np.testing.assert_allclose(out, ref, rtol=0, atol=1e-13 * np.abs(ref).max())


def test_cpu_features_buffer() -> None:
    buf = ctypes.create_string_buffer(8)
    n = nb.lib().advtg_cpu_features(buf, len(buf))
    assert n > 0 and len(buf.value) <= 7
