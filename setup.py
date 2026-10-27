"""Build configuration for Advanced Timegrapher native C++ extensions."""

import sys

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

extra_compile_args = []
extra_link_args = []

if sys.platform == "win32":
    extra_compile_args = ["/O2", "/fp:fast", "/std:c++17"]
else:
    # -ffast-math for speed, but -fno-finite-math-only keeps NaN/inf meaningful; every
    # division in native_dsp.cpp is noise gated (kGate) so none can blow up.
    extra_compile_args = ["-O3", "-ffast-math", "-fno-finite-math-only", "-std=c++17"]

ext_modules = [
    Pybind11Extension(
        "adv_tg.dsp.adv_tg_dsp_native",
        ["src/adv_tg/dsp/native_dsp.cpp"],
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
