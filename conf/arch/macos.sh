#!/usr/bin/env bash

export OC_WORK="${HOME}/Desktop/opencarp-work"
export OC32_BIN="${OC_SRC}/build_32bit/bin/openCARP"
export OC64_BIN="${OC_SRC}/build_64bit/bin/openCARP"
export GMSH="/Applications/Gmsh.app/Contents/MacOS/gmsh"

OC_SRC="${OC_WORK}/src/openCARP"
OC_EXT="${OC_WORK}/external"

# Not exporting arch to avoid conflicts when testing 32 and 64 bits
export PETSC_DIR="${OC_EXT}/petsc-3.25.1"
#export PETSC_ARCH="arch-64-double-opt"
#export LLVM_DIR="${OC_EXT}/llvm-project-18.1.8.src"
#

#alias ocbuild='cd "${OC_SRC}"'
#alias ocwork='cd "${OC_WORK}"'
#alias ocscripts='cd "${OC_SCRIPTS}"'

# Run openCARP from anywhere
alias oc64='${OC64_BIN}'
alias oc32='${OC32_BIN}'
alias gmsh='${GMSH}'

# activate uv environment if present
if [ -f "${OC_WORK}/.venv/bin/activate" ]; then
    source "${OC_WORK}/.venv/bin/activate"
fi

echo "Loaded macos.env"
echo "  OC_WORK   = ${OC_WORK}"
echo "  OC32_BIN  = ${OC32_BIN}"
echo "  OC64_BIN  = ${OC64_BIN}"
echo "  PETSC_DIR = ${PETSC_DIR}"
