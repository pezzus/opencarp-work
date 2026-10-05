#!/usr/bin/env bash
set -euo pipefail

LLVM_BASEURL=https://github.com/llvm/llvm-project/releases/download
LLVM_VERSION=18.1.8

# Directory from which the script was launched.
ROOT_DIR="$(pwd)"

# Persistent installation.
LLVM_INSTALL="${ROOT_DIR}/llvm-${LLVM_VERSION}"

HOST="$(hostname -s)"

# On a Daint compute node HOST will normally be nid..., not daint-*.
if [[ "${HOST}" == daint-* || "${SLURM_CLUSTER_NAME:-}" == "daint" ]]; then
  PLATFORM="cscs"
  export CC=gcc
  export CXX=g++
else
  PLATFORM="local"
fi

# ---------------------------------------------------------------------------
# Build workspace
#
# LLVM creates a very large number of small files. On Daint use the node-local
# tmpfs. The complete workspace is disposable; only LLVM_INSTALL is persistent.
#
# LLVM_BUILD_ROOT can be set explicitly to override this choice, e.g.
#
#   LLVM_BUILD_ROOT="$SCRATCH/llvm-build" ./config-llvm.sh
# ---------------------------------------------------------------------------

if [[ -n "${LLVM_BUILD_ROOT:-}" ]]; then
  WORK_DIR="${LLVM_BUILD_ROOT}"
elif [[ "${PLATFORM}" == "cscs" ]]; then
  WORK_DIR="/dev/shm/${USER}/llvm-${LLVM_VERSION}-${SLURM_JOB_ID:-$$}"
else
  WORK_DIR="${ROOT_DIR}/llvm-project-${LLVM_VERSION}.work"
fi

LLVM_SRC="${WORK_DIR}/llvm-project-${LLVM_VERSION}.src"
LLVM_BUILD="${WORK_DIR}/build"

echo "Host         : ${HOST}"
echo "Platform     : ${PLATFORM}"
echo "C compiler   : $(command -v "${CC:-cc}")"
echo "C++ compiler : $(command -v "${CXX:-c++}")"
echo "Workspace    : ${WORK_DIR}"
echo "Source       : ${LLVM_SRC}"
echo "Build        : ${LLVM_BUILD}"
echo "Install      : ${LLVM_INSTALL}"

CMAKE_COMPILER_OPTIONS=()

if [[ "${PLATFORM}" == "cscs" ]]; then
  CMAKE_COMPILER_OPTIONS+=(
    "-DCMAKE_C_COMPILER=$(command -v gcc)"
    "-DCMAKE_CXX_COMPILER=$(command -v g++)"
  )
fi

mkdir -p "${WORK_DIR}"

# ---------------------------------------------------------------------------
# LLVM source
# ---------------------------------------------------------------------------

if [[ ! -d "${LLVM_SRC}" ]]; then
  echo "Downloading LLVM ${LLVM_VERSION}..."

  curl -L \
    "${LLVM_BASEURL}/llvmorg-${LLVM_VERSION}/llvm-project-${LLVM_VERSION}.src.tar.xz" \
    | tar -xJ -C "${WORK_DIR}"
else
  echo "Directory ${LLVM_SRC} already exists, skipping download."
fi

# ---------------------------------------------------------------------------
# pybind11 workaround (< 3.x)
# ---------------------------------------------------------------------------

PYTHON_BIN="${VIRTUAL_ENV}/bin/python"

PYBIND11_VER=2.11.1
PYBIND11_SRC="${WORK_DIR}/pybind11-${PYBIND11_VER}"
PYBIND11_BUILD="${WORK_DIR}/pybind11-build"
PYBIND11_INSTALL="${WORK_DIR}/pybind11-install"

if [[ ! -d "${PYBIND11_SRC}" ]]; then
  echo "Downloading pybind11 ${PYBIND11_VER}..."

  curl -L \
    "https://github.com/pybind/pybind11/archive/refs/tags/v${PYBIND11_VER}.tar.gz" \
    | tar -xz -C "${WORK_DIR}"

  # GitHub extracts as pybind11-${PYBIND11_VER}, matching PYBIND11_SRC.
fi

cmake \
  -S "${PYBIND11_SRC}" \
  -B "${PYBIND11_BUILD}" \
  -G Ninja \
  "${CMAKE_COMPILER_OPTIONS[@]}" \
  -DCMAKE_INSTALL_PREFIX="${PYBIND11_INSTALL}" \
  -DPYBIND11_TEST=OFF

cmake --build "${PYBIND11_BUILD}"
cmake --install "${PYBIND11_BUILD}"

# ---------------------------------------------------------------------------
# LLVM / MLIR
# ---------------------------------------------------------------------------

echo "Configuring LLVM..."

cmake \
  -S "${LLVM_SRC}/llvm" \
  -B "${LLVM_BUILD}" \
  -G Ninja \
  "${CMAKE_COMPILER_OPTIONS[@]}" \
  -DLLVM_ENABLE_PROJECTS="mlir;clang;lld" \
  -DLLVM_TARGETS_TO_BUILD="host" \
  -DLLVM_ENABLE_RUNTIMES="openmp;compiler-rt;libcxx;libcxxabi;libunwind" \
  -DCMAKE_BUILD_TYPE=Release \
  -DMLIR_ENABLE_BINDINGS_PYTHON=ON \
  -DPython3_EXECUTABLE="${PYTHON_BIN}" \
  -DPYBIND11_FINDPYTHON=ON \
  -Dpybind11_DIR="${PYBIND11_INSTALL}/share/cmake/pybind11" \
  -DBUILD_SHARED_LIBS=ON \
  -DLLVM_INSTALL_UTILS=ON \
  -DCMAKE_INSTALL_PREFIX="${LLVM_INSTALL}"

echo "Building LLVM..."
cmake --build "${LLVM_BUILD}" --parallel 8 --target install

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

echo
echo "LLVM ${LLVM_VERSION} installed in:"
echo "  ${LLVM_INSTALL}"
echo
echo "Build workspace:"
echo "  ${WORK_DIR}"
