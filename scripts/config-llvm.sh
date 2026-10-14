#!/usr/bin/env bash
set -euo pipefail

LLVM_BASEURL=https://github.com/llvm/llvm-project/releases/download
LLVM_VERSION="${LLVM_VERSION:-22.1.8}"

# Resolve all persistent paths relative to this script so the result does not
# depend on the directory from which it is invoked.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPS_DIR="${PROJECT_DIR}/deps"

# Persistent installation.
LLVM_INSTALL="${DEPS_DIR}/llvm-${LLVM_VERSION}"

HOST="$(hostname -s)"

# On a Daint compute node HOST will normally be nid..., not daint-*.
if [[ "${HOST}" == daint-* || "${HOST}" == nid* || "${SLURM_CLUSTER_NAME:-}" == "daint" ]]; then
  PLATFORM="cscs"
else
  PLATFORM="local"
fi

# Use the selected GCC toolchain on both the workstation and CSCS. Environment
# overrides remain possible, for example CC=gcc-14 CXX=g++-14.
CC="${CC:-gcc}"
CXX="${CXX:-g++}"
export CC="$(command -v "${CC}")"
export CXX="$(command -v "${CXX}")"

UV="${UV:-$(command -v uv)}"
if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  echo "Error: the uv environment is missing: ${PROJECT_DIR}/.venv" >&2
  echo "Run 'uv sync' in ${PROJECT_DIR} first." >&2
  exit 1
fi

# Resolve Python through the project environment managed by uv. --no-sync keeps
# this build script from unexpectedly changing that environment. LLVM's Python
# build dependencies are installed privately below; the project's resolved
# dependency set is not modified.
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/opencarp-uv-cache-${UID}}"
export UV_CACHE_DIR
PYTHON_BIN="$("${UV}" run --project "${PROJECT_DIR}" --no-sync python -c 'import sys; print(sys.executable)')"

# LLVM/MLIR 22 uses nanobind for its Python bindings. Keep its build-time
# Python dependencies isolated from openCARP's uv environment. The nanobind
# range follows LLVM 22's own requirements while allowing a Python 3.14-aware
# 2.x release to be selected.
NANOBIND_REQUIREMENT="${NANOBIND_REQUIREMENT:-nanobind>=2.9,<3}"

# ---------------------------------------------------------------------------
# Build workspace
#
# LLVM creates a very large number of small files. On Daint use the node-local
# tmpfs. On a workstation, keep the build below deps: /tmp and /dev/shm are
# often too small, and this location is already ignored by the workspace.
# The complete workspace is disposable; only LLVM_INSTALL is an end product.
#
# LLVM_BUILD_ROOT can be set explicitly to override this choice, e.g.
#
#   LLVM_BUILD_ROOT="$SCRATCH/llvm-build" ./config-llvm.sh
# ---------------------------------------------------------------------------

if [[ -n "${LLVM_BUILD_ROOT:-}" ]]; then
  WORK_DIR="${LLVM_BUILD_ROOT}"
elif [[ "${PLATFORM}" == "cscs" ]]; then
  WORK_DIR="/dev/shm/${USER}/llvm-${LLVM_VERSION}-${SLURM_JOB_ID:-interactive}"
else
  WORK_DIR="${DEPS_DIR}/.build/llvm-${LLVM_VERSION}"
fi

LLVM_SRC="${WORK_DIR}/llvm-project-${LLVM_VERSION}.src"
LLVM_BUILD="${WORK_DIR}/build"

# Select CUDA explicitly. The NVPTX target itself does not require a toolkit,
# but the MLIR CUDA runner and the post-install Clang check do.
if [[ -n "${LLVM_CUDAC:-}" ]]; then
  CUDA_NVCC="${LLVM_CUDAC}"
elif command -v nvcc >/dev/null 2>&1; then
  CUDA_NVCC="$(command -v nvcc)"
elif [[ -n "${CUDA_HOME:-}" && -x "${CUDA_HOME}/bin/nvcc" ]]; then
  CUDA_NVCC="${CUDA_HOME}/bin/nvcc"
elif [[ -x /usr/local/cuda/bin/nvcc ]]; then
  CUDA_NVCC="/usr/local/cuda/bin/nvcc"
else
  echo "Error: nvcc was not found." >&2
  echo "Load CUDA, set CUDA_HOME, or set LLVM_CUDAC=/path/to/nvcc." >&2
  exit 1
fi

if [[ ! -x "${CUDA_NVCC}" ]]; then
  echo "Error: CUDA compiler is not executable: ${CUDA_NVCC}" >&2
  exit 1
fi

CUDA_ROOT="$(cd "$(dirname "${CUDA_NVCC}")/.." && pwd)"
if [[ "${PLATFORM}" == "cscs" ]]; then
  CUDA_ARCH="${LLVM_CUDA_ARCH:-90}"
else
  CUDA_ARCH="${LLVM_CUDA_ARCH:-89}"
fi

if ! [[ "${CUDA_ARCH}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: LLVM_CUDA_ARCH must be numeric (for example 89 or 90)." >&2
  exit 1
fi

echo "Host         : ${HOST}"
echo "Platform     : ${PLATFORM}"
echo "C compiler   : ${CC}"
echo "C++ compiler : ${CXX}"
echo "Python       : ${PYTHON_BIN}"
echo "CUDA compiler: ${CUDA_NVCC}"
echo "CUDA root    : ${CUDA_ROOT}"
echo "CUDA arch    : ${CUDA_ARCH} (sm_${CUDA_ARCH})"
echo "Workspace    : ${WORK_DIR}"
echo "Source       : ${LLVM_SRC}"
echo "Build        : ${LLVM_BUILD}"
echo "Install      : ${LLVM_INSTALL}"

CMAKE_COMPILER_OPTIONS=(
  "-DCMAKE_C_COMPILER=${CC}"
  "-DCMAKE_CXX_COMPILER=${CXX}"
)

mkdir -p "${DEPS_DIR}" "${WORK_DIR}"

# ---------------------------------------------------------------------------
# LLVM source
# ---------------------------------------------------------------------------

if [[ ! -d "${LLVM_SRC}" ]]; then
  echo "Downloading LLVM ${LLVM_VERSION}..."

  curl -fL --retry 3 \
    "${LLVM_BASEURL}/llvmorg-${LLVM_VERSION}/llvm-project-${LLVM_VERSION}.src.tar.xz" \
    | tar -xJ -C "${WORK_DIR}"
else
  echo "Directory ${LLVM_SRC} already exists, skipping download."
fi

# ---------------------------------------------------------------------------
# Private Python build dependencies
#
# LLVM 22 migrated the MLIR bindings from pybind11 to nanobind. Installing into
# a private target directory avoids changing the pybind11 and NumPy versions
# required by openCARP's main uv environment.
# ---------------------------------------------------------------------------

# Keep these with the installed LLVM rather than in the disposable build tree:
# openCARP includes MLIR's CMake configuration and needs nanobind at its own
# configure time as well.
LLVM_PYTHON_DEPS="${LLVM_INSTALL}/python_build_deps"
mkdir -p "${LLVM_PYTHON_DEPS}"

echo "Installing private MLIR Python build dependencies..."
"${UV}" pip install \
  --python "${PYTHON_BIN}" \
  --target "${LLVM_PYTHON_DEPS}" \
  --upgrade \
  "${NANOBIND_REQUIREMENT}" \
  "PyYAML>=5.4" \
  "typing_extensions>=4.12.2"

export PYTHONPATH="${LLVM_PYTHON_DEPS}${PYTHONPATH:+:${PYTHONPATH}}"
NANOBIND_DIR="$("${PYTHON_BIN}" -c 'import nanobind; print(nanobind.cmake_dir())')"

# ---------------------------------------------------------------------------
# LLVM / MLIR
# ---------------------------------------------------------------------------

case "$(uname -m)" in
  aarch64|arm64) LLVM_TARGETS="AArch64;NVPTX" ;;
  x86_64|amd64) LLVM_TARGETS="X86;NVPTX" ;;
  *) LLVM_TARGETS="host;NVPTX" ;;
esac

LLVM_BUILD_JOBS="${LLVM_BUILD_JOBS:-8}"
LLVM_RUNTIMES="${LLVM_RUNTIMES:-openmp}"

echo "Configuring LLVM..."
echo "LLVM targets  : ${LLVM_TARGETS}"
echo "LLVM runtimes : ${LLVM_RUNTIMES}"
echo "Build jobs    : ${LLVM_BUILD_JOBS}"
echo "nanobind      : ${NANOBIND_REQUIREMENT} (${NANOBIND_DIR})"

cmake \
  -S "${LLVM_SRC}/llvm" \
  -B "${LLVM_BUILD}" \
  -G Ninja \
  "${CMAKE_COMPILER_OPTIONS[@]}" \
  -DLLVM_ENABLE_PROJECTS="mlir;clang;lld" \
  -DLLVM_TARGETS_TO_BUILD="${LLVM_TARGETS}" \
  -DLLVM_ENABLE_RUNTIMES="${LLVM_RUNTIMES}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DMLIR_ENABLE_BINDINGS_PYTHON=ON \
  -DMLIR_ENABLE_CUDA_RUNNER=ON \
  -DPython_EXECUTABLE="${PYTHON_BIN}" \
  -DPython3_EXECUTABLE="${PYTHON_BIN}" \
  -Dnanobind_DIR="${NANOBIND_DIR}" \
  -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
  -DCMAKE_CUDA_COMPILER="${CUDA_NVCC}" \
  -DCMAKE_CUDA_HOST_COMPILER="${CXX}" \
  -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH}" \
  -DBUILD_SHARED_LIBS=ON \
  -DLLVM_INSTALL_UTILS=ON \
  -DCMAKE_INSTALL_PREFIX="${LLVM_INSTALL}"

echo "Building LLVM..."
cmake --build "${LLVM_BUILD}" --parallel "${LLVM_BUILD_JOBS}" --target install

# ---------------------------------------------------------------------------
# Python MLIR package
# ---------------------------------------------------------------------------

echo "Installing LLVM-MLIR Python path..."

LLVM_INSTALL="${LLVM_INSTALL}" "${PYTHON_BIN}" - <<'PY'
import os
import site
from pathlib import Path

llvm_install = Path(os.environ["LLVM_INSTALL"])
mlir_path = (llvm_install / "python_packages" / "mlir_core").resolve()

for sp in site.getsitepackages():
    pth = Path(sp) / "mlir.pth"
    pth.write_text(str(mlir_path) + "\n")
    print(f"Wrote {pth}")
    break
PY

# ---------------------------------------------------------------------------
# macOS rpath fix
# ---------------------------------------------------------------------------

if [[ "$(uname -s)" == "Darwin" ]]; then
  MLIR_EXT="$(find \
    "${LLVM_INSTALL}/python_packages/mlir_core/mlir/_mlir_libs" \
    -maxdepth 1 \
    -name '_mlir*.so' \
    -print -quit)"

  install_name_tool \
    -add_rpath "${LLVM_INSTALL}/lib" \
    "${MLIR_EXT}"
fi

# ---------------------------------------------------------------------------
# Installation checks
# ---------------------------------------------------------------------------

echo "Checking the LLVM installation..."

if ! "${LLVM_INSTALL}/bin/llvm-config" --version | grep -qx "${LLVM_VERSION}"; then
  echo "Error: installed llvm-config does not report ${LLVM_VERSION}." >&2
  exit 1
fi

if ! "${LLVM_INSTALL}/bin/llc" --version | grep -q 'nvptx'; then
  echo "Error: the installed LLVM does not contain the NVPTX target." >&2
  exit 1
fi

echo "Checking Clang CUDA compilation for sm_${CUDA_ARCH}..."
printf '%s\n' \
  '#include <cuda_runtime.h>' \
  '__global__ void opencarp_cuda_probe(double *x) { x[threadIdx.x] += 1.0; }' \
  | "${LLVM_INSTALL}/bin/clang++" \
      -x cuda \
      --cuda-path="${CUDA_ROOT}" \
      --cuda-gpu-arch="sm_${CUDA_ARCH}" \
      --cuda-device-only \
      -nocudalib \
      -S -emit-llvm \
      -o /dev/null -

LD_LIBRARY_PATH="${LLVM_INSTALL}/lib:${LD_LIBRARY_PATH:-}" \
PYTHONPATH="${LLVM_INSTALL}/python_packages/mlir_core:${PYTHONPATH:-}" \
  "${PYTHON_BIN}" -c 'from mlir import ir; print(f"MLIR Python bindings: {ir.__file__}")'

echo
echo "LLVM ${LLVM_VERSION} installed in:"
echo "  ${LLVM_INSTALL}"
echo
echo "Build workspace:"
echo "  ${WORK_DIR}"
echo
echo "Add LLVM tools and Python bindings to the environment with:"
echo "  export PATH=\"${LLVM_INSTALL}/bin:\${PATH}\""
echo "  export PYTHONPATH=\"${LLVM_INSTALL}/python_packages/mlir_core:\${PYTHONPATH:-}\""
