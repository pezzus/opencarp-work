#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${HOME}/Desktop/opencarp-work"
PETSC_DIR="${WORK_DIR}/deps/petsc-3.25.5"
#LLVM_DIR="${WORK_DIR}/deps/llvm-project-18.1.8.src/install"

OPENCARP_DIR="${WORK_DIR}/src/openCARP"

echo "PETSC_DIR = ${PETSC_DIR}"
#echo "LLVM_DIR  = ${LLVM_DIR}"
echo "OPENCARP_DIR = ${OPENCARP_DIR}"

usage() {
  cat <<EOF
Usage:
  $0 [int]

Arguments:
  int       32 | 64         default: 64

Examples:
  $0 32
  $0 64
EOF
}

INT_TYPE="${1:-64}"

case "${INT_TYPE}" in
  32|64) ;;
  *)
    echo "Error: int must be '32' or '64', got '${INT_TYPE}'" >&2
    usage
    exit 1
    ;;
esac

echo "Configuring openCARP..."
cd "${OPENCARP_DIR}"

PETSC_DIR="${PETSC_DIR}" \
PETSC_ARCH="arch-${INT_TYPE}-double-opt" \
cmake -S . -B "build_${INT_TYPE}bit" -G Ninja \
    -DDLOPEN=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_GINKGO=ON \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_OSX_SYSROOT="$(xcrun --sdk macosx --show-sdk-path)" \
    -DUSE_OPENMP=OFF \
    -DENABLE_PETSC=ON \
    -DOPENCARP_GLOBAL_INDEX_BITS="${INT_TYPE}" \
    -DOPENCARP_LOCAL_INDEX_BITS="${INT_TYPE}" \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DBUILD_EXTERNAL=OFF
#    -DCMAKE_PREFIX_PATH="${LLVM_DIR}" \
#    -DENABLE_MLIR_CODEGEN=ON \

echo "Building openCARP ${INT_TYPE}-bit..."
cmake --build "build_${INT_TYPE}bit" --parallel 6

echo
echo "Done."
echo "openCARP bin = ${OPENCARP_DIR}/build_${INT_TYPE}bit/bin/openCARP"
