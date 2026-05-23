#!/usr/bin/env bash
set -euo pipefail

LLVM_BASEURL=https://github.com/llvm/llvm-project/releases/download
LLVM_VERSION=18.1.8
LLVM_DIR=$(pwd)/llvm-project-${LLVM_VERSION}.src

if [[ ! -d "${LLVM_DIR}" ]]; then
  echo "Downloading LLVM ${LLVM_VERSION}..."
  curl -L "${LLVM_BASEURL}/llvmorg-${LLVM_VERSION}/llvm-project-${LLVM_VERSION}.src.tar.xz" | tar xJ
else
  echo "Directory ${LLVM_DIR} already exists, skipping download."
fi

cd "${LLVM_DIR}"

# workaround pybind11 < 3.x

PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
PYBIND11_VER=2.11.1
PYBIND11_SRC="${PWD}/third-party/pybind11-${PYBIND11_VER}"

if [ ! -d "${PYBIND11_SRC}" ]; then
  mkdir -p third-party
  curl -L "https://github.com/pybind/pybind11/archive/refs/tags/v${PYBIND11_VER}.tar.gz" \
    | tar -xz -C third-party
fi
PYBIND11_INSTALL="${PWD}/third-party/pybind11-install"

cmake -S "${PYBIND11_SRC}" -B "${PYBIND11_SRC}/build" -G Ninja \
  -DCMAKE_INSTALL_PREFIX="${PYBIND11_INSTALL}" \
  -DPYBIND11_TEST=OFF

cmake --build "${PYBIND11_SRC}/build"
cmake --install "${PYBIND11_SRC}/build"

echo "Configuring LLVM..."
cmake -S. -B build -G Ninja llvm \
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
    -DCMAKE_INSTALL_PREFIX="${LLVM_DIR}/install"

echo "Building LLVM..."
cmake --build build --parallel 8 --target install

echo "Installing LLVM-MLIR..."
python - <<'PY'
import site
from pathlib import Path

mlir_path = Path("install/python_packages/mlir_core").resolve()

for sp in site.getsitepackages():
    pth = Path(sp) / "mlir.pth"
    pth.write_text(str(mlir_path) + "\n")
    print(f"Wrote {pth}")
    break
PY

install_name_tool -add_rpath "${LLVM_DIR}/install/lib" \
    ${LLVM_DIR}/install/python_packages/mlir_core/mlir/_mlir_libs/_mlir.cpython-314-darwin.so
