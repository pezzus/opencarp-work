#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PETSC_VERSION=3.25.5
LLVM_VERSION=18.1.8

# Resolve workspace relative to this script, regardless of current directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PETSC_DIR="${WORK_DIR}/deps/petsc-${PETSC_VERSION}"
LLVM_DIR="${WORK_DIR}/deps/llvm-${LLVM_VERSION}"
OPENCARP_DIR="${WORK_DIR}/src/openCARP"

usage() {
  cat <<EOF
Usage:
  $0 [int]

Arguments:
  int       32 | 64         default: 64

Environment:
  OPENCARP_BUILD_JOBS   Number of parallel build jobs (default: Ninja auto)

Examples:
  $0
  $0 32
  $0 64
  OPENCARP_BUILD_JOBS=128 $0 64
EOF
}

INT_TYPE="${1:-32}"

case "${INT_TYPE}" in
  32|64) ;;
  *)
    echo "Error: int must be '32' or '64', got '${INT_TYPE}'" >&2
    usage
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

HOST="$(hostname -s)"

case "${HOST}" in
  daint-*|nid[0-9]*)
    PLATFORM="cscs"
    export CC="$(command -v mpicc)"
    export CXX="$(command -v mpicxx)"
    ;;
  *)
    PLATFORM="local"
    ;;
esac

# ---------------------------------------------------------------------------
# PETSc configuration
# ---------------------------------------------------------------------------

export PETSC_DIR
export PETSC_ARCH="arch-${INT_TYPE}-double-opt"

if [[ ! -d "${PETSC_DIR}" ]]; then
  echo "Error: PETSc directory not found: ${PETSC_DIR}" >&2
  exit 1
fi

if [[ ! -d "${PETSC_DIR}/${PETSC_ARCH}" ]]; then
  echo "Error: PETSc architecture not found: ${PETSC_ARCH}" >&2
  echo "Build PETSc first using config-petsc.sh" >&2
  exit 1
fi

if [[ ! -d "${OPENCARP_DIR}" ]]; then
  echo "Error: openCARP directory not found: ${OPENCARP_DIR}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# CMake options
# ---------------------------------------------------------------------------

BUILD_DIR="${OPENCARP_DIR}/build_${INT_TYPE}bit"

CMAKE_OPTIONS=(
  "-DDLOPEN=ON"
  "-DCMAKE_BUILD_TYPE=Release"
  "-DENABLE_GINKGO=OFF"
  "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
  "-DENABLE_PETSC=ON"
  "-DOPENCARP_GLOBAL_INDEX_BITS=${INT_TYPE}"
  "-DOPENCARP_LOCAL_INDEX_BITS=${INT_TYPE}"
  "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
  "-DBUILD_EXTERNAL=OFF"
)

if [[ "${PLATFORM}" == "cscs" ]]; then
    CUDA_NVCC="$(command -v nvcc)"

    export LD_LIBRARY_PATH="${LLVM_DIR}/lib:${LLVM_DIR}/python_packages/mlir_core/mlir/_mlir_libs:${LD_LIBRARY_PATH:-}"

    CMAKE_OPTIONS+=(
        "-DCMAKE_C_COMPILER=$(command -v mpicc)"
        "-DCMAKE_CXX_COMPILER=$(command -v mpicxx)"
        "-DCMAKE_PREFIX_PATH=${LLVM_DIR}"
        "-DENABLE_MLIR_CODEGEN=ON"
        "-DUSE_OPENMP=ON"
        "-DCUDAToolkit_ROOT=$(dirname "$(dirname "${CUDA_NVCC}")")"
        "-DCMAKE_CUDA_ARCHITECTURES=90"
        "-DCUDA_GPU_ARCH=sm_90"
    )
fi

# macOS-specific options
if [[ "$(uname -s)" == "Darwin" ]]; then
  CMAKE_OPTIONS+=(
    "-DCMAKE_OSX_SYSROOT=$(xcrun --sdk macosx --show-sdk-path)"
    "-DUSE_OPENMP=OFF"
  )
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo "Host         : ${HOST}"
echo "Platform     : ${PLATFORM}"
echo "Workspace    : ${WORK_DIR}"
echo "PETSC_DIR    : ${PETSC_DIR}"
echo "PETSC_ARCH   : ${PETSC_ARCH}"
echo "LLVM_DIR     : ${LLVM_DIR}"
echo "OPENCARP_DIR : ${OPENCARP_DIR}"
echo "Build        : ${BUILD_DIR}"
echo "C compiler   : $(command -v "${CC:-cc}")"
echo "C++ compiler : $(command -v "${CXX:-c++}")"

# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------

echo "Configuring openCARP..."

cmake \
  -S "${OPENCARP_DIR}" \
  -B "${BUILD_DIR}" \
  -G Ninja \
  "${CMAKE_OPTIONS[@]}"

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

echo "Building openCARP ${INT_TYPE}-bit..."

if [[ -n "${OPENCARP_BUILD_JOBS:-}" ]]; then
  cmake --build "${BUILD_DIR}" --parallel "${OPENCARP_BUILD_JOBS}"
else
  cmake --build "${BUILD_DIR}"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
echo "Done."
echo "openCARP bin = ${BUILD_DIR}/bin/openCARP"
