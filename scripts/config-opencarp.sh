#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PETSC_VERSION="${PETSC_VERSION:-3.26.0}"
LLVM_VERSION="${LLVM_VERSION:-22.1.8}"

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
  int       32 | 64         default: 32

Environment:
  OPENCARP_BUILD_JOBS   Number of parallel build jobs (default: Ninja auto)
  OPENCARP_BUILD_DIR    Build directory (default includes LLVM version)
  OPENCARP_CUDA_ARCH    CUDA compute capability (default: 89 locally, 90 on CSCS)
  OPENCARP_CUDAC        Path to nvcc
  PETSC_VERSION         PETSc version to use (default: 3.26.0)
  LLVM_VERSION          LLVM version to use (default: 22.1.8)

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
    ;;
  *)
    if [[ "${SLURM_CLUSTER_NAME:-}" == "daint" ]]; then
      PLATFORM="cscs"
    else
      PLATFORM="local"
    fi
    ;;
esac

if [[ "${PLATFORM}" == "cscs" ]]; then
  CUDA_ARCH="${OPENCARP_CUDA_ARCH:-90}"
else
  CUDA_ARCH="${OPENCARP_CUDA_ARCH:-89}"
fi

if [[ -n "${OPENCARP_CUDAC:-}" ]]; then
  CUDA_NVCC="${OPENCARP_CUDAC}"
elif command -v nvcc >/dev/null 2>&1; then
  CUDA_NVCC="$(command -v nvcc)"
elif [[ -n "${CUDA_HOME:-}" && -x "${CUDA_HOME}/bin/nvcc" ]]; then
  CUDA_NVCC="${CUDA_HOME}/bin/nvcc"
elif [[ -x /usr/local/cuda/bin/nvcc ]]; then
  CUDA_NVCC="/usr/local/cuda/bin/nvcc"
else
  echo "Error: nvcc was not found." >&2
  echo "Load CUDA, set CUDA_HOME, or set OPENCARP_CUDAC=/path/to/nvcc." >&2
  exit 1
fi

if [[ ! -x "${CUDA_NVCC}" ]]; then
  echo "Error: CUDA compiler is not executable: ${CUDA_NVCC}" >&2
  exit 1
fi

CUDA_ROOT="$(cd "$(dirname "${CUDA_NVCC}")/.." && pwd)"

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

if [[ "${PLATFORM}" == "cscs" ]]; then
  if ! command -v mpicc >/dev/null 2>&1 || ! command -v mpicxx >/dev/null 2>&1; then
    echo "Error: system MPI compiler wrappers mpicc and mpicxx were not found." >&2
    echo "Load the same MPI environment used to build PETSc." >&2
    exit 1
  fi
  export CC="$(command -v mpicc)"
  export CXX="$(command -v mpicxx)"
else
  PETSC_MPI_BIN="${PETSC_DIR}/${PETSC_ARCH}/bin"

  if [[ ! -x "${PETSC_MPI_BIN}/mpicc" || ! -x "${PETSC_MPI_BIN}/mpicxx" ]]; then
    echo "Error: PETSc's bundled MPICH compiler wrappers were not found in:" >&2
    echo "  ${PETSC_MPI_BIN}" >&2
    exit 1
  fi

  export CC="${PETSC_MPI_BIN}/mpicc"
  export CXX="${PETSC_MPI_BIN}/mpicxx"
  export PATH="${PETSC_MPI_BIN}:${PATH}"
fi
export PATH="${LLVM_DIR}/bin:${PATH}"

if [[ ! -d "${OPENCARP_DIR}" ]]; then
  echo "Error: openCARP directory not found: ${OPENCARP_DIR}" >&2
  exit 1
fi

if [[ ! -f "${LLVM_DIR}/lib/cmake/mlir/MLIRConfig.cmake" ||
      ! -f "${LLVM_DIR}/lib/cmake/llvm/LLVMConfig.cmake" ]]; then
  echo "Error: LLVM/MLIR installation is incomplete: ${LLVM_DIR}" >&2
  echo "Build LLVM first using config-llvm.sh" >&2
  exit 1
fi

if [[ ! -x "${WORK_DIR}/.venv/bin/python" ]]; then
  echo "Error: the uv environment is missing: ${WORK_DIR}/.venv" >&2
  echo "Run 'uv sync' in ${WORK_DIR} first." >&2
  exit 1
fi

PYTHON_BIN="${WORK_DIR}/.venv/bin/python"
MLIR_PYTHON_DIR="${LLVM_DIR}/python_packages/mlir_core"
LLVM_PYTHON_BUILD_DEPS="${LLVM_DIR}/python_build_deps"

export PYTHONPATH="${LLVM_PYTHON_BUILD_DEPS}:${MLIR_PYTHON_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export LD_LIBRARY_PATH="${LLVM_DIR}/lib:${MLIR_PYTHON_DIR}/mlir/_mlir_libs${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

if ! "${PYTHON_BIN}" -c 'import mlir.ir, nanobind, pybind11' >/dev/null 2>&1; then
  echo "Error: Python cannot import MLIR, nanobind, and pybind11." >&2
  echo "Check the LLVM Python installation and run 'uv sync' in ${WORK_DIR}." >&2
  exit 1
fi

NANOBIND_DIR="$(${PYTHON_BIN} -c 'import nanobind; print(nanobind.cmake_dir())')"

# ---------------------------------------------------------------------------
# CMake options
# ---------------------------------------------------------------------------

BUILD_DIR="${OPENCARP_BUILD_DIR:-${OPENCARP_DIR}/build_${INT_TYPE}bit}"

CMAKE_OPTIONS=(
  "-DDLOPEN=ON"
  "-DCMAKE_BUILD_TYPE=Release"
  "-DCMAKE_C_COMPILER=${CC}"
  "-DCMAKE_CXX_COMPILER=${CXX}"
  "-DCMAKE_CUDA_COMPILER=${CUDA_NVCC}"
  "-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH}"
  "-DCMAKE_PREFIX_PATH=${LLVM_DIR}"
  "-DLLVM_DIR=${LLVM_DIR}/lib/cmake/llvm"
  "-DMLIR_DIR=${LLVM_DIR}/lib/cmake/mlir"
  "-DPython_EXECUTABLE=${PYTHON_BIN}"
  "-DPython3_EXECUTABLE=${PYTHON_BIN}"
  "-Dnanobind_DIR=${NANOBIND_DIR}"
  "-Dpybind11_DIR=$(${PYTHON_BIN} -c 'import pybind11; print(pybind11.get_cmake_dir())')"
  "-DCUDAToolkit_ROOT=${CUDA_ROOT}"
  "-DCUDA_GPU_ARCH=sm_${CUDA_ARCH}"
  "-DENABLE_GINKGO=OFF"
  "-DENABLE_MLIR_CODEGEN=ON"
  "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
  "-DENABLE_PETSC=ON"
  "-DMLIR_CUDA_DISABLED=OFF"
  "-DMLIR_ROCM_DISABLED=ON"
  "-DOPENCARP_GLOBAL_INDEX_BITS=${INT_TYPE}"
  "-DOPENCARP_LOCAL_INDEX_BITS=${INT_TYPE}"
  "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
  "-DBUILD_EXTERNAL=OFF"
  "-DUSE_OPENMP=ON"
)

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
echo "Python       : ${PYTHON_BIN}"
echo "CUDA compiler: ${CUDA_NVCC}"
echo "CUDA arch    : ${CUDA_ARCH} (sm_${CUDA_ARCH})"
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
