#!/usr/bin/env bash
set -euo pipefail

PETSC_BASEURL="https://web.cels.anl.gov/projects/petsc/download/release-snapshots"

usage() {
  cat <<EOF
Usage:
  $0 [version] [real] [int] [build]

Arguments:
  version   PETSc version                 default: 3.25.5
  real      single | double               default: double
  int       32 | 64                       default: 32
  build     opt | dbg                     default: opt

Examples:
  $0
  $0 3.25.0
  $0 3.25.0 double 64 dbg
  $0 3.24.1 single 32 opt
EOF
}

PETSC_VERSION="${1:-3.25.5}"
REAL_TYPE="${2:-double}"
INT_TYPE="${3:-32}"
BUILD_TYPE="${4:-opt}"

case "${REAL_TYPE}" in
  single|double) ;;
  *)
    echo "Error: real must be 'single' or 'double', got '${REAL_TYPE}'" >&2
    usage
    exit 1
    ;;
esac

case "${INT_TYPE}" in
  32|64) ;;
  *)
    echo "Error: int must be '32' or '64', got '${INT_TYPE}'" >&2
    usage
    exit 1
    ;;
esac

case "${BUILD_TYPE}" in
  opt|dbg) ;;
  *)
    echo "Error: build must be 'opt' or 'dbg', got '${BUILD_TYPE}'" >&2
    usage
    exit 1
    ;;
esac

HOST="$(hostname -s)"

case "${HOST}" in
  daint-*)
    PLATFORM="cscs"
    ;;
  *)
    PLATFORM="local"
    ;;
esac

if [[ "${INT_TYPE}" == "64" ]]; then
  WITH_64_BIT_INDICES=1
else
  WITH_64_BIT_INDICES=0
fi

if [[ "${BUILD_TYPE}" == "dbg" ]]; then
  WITH_DEBUGGING=1
else
  WITH_DEBUGGING=0
fi

PETSC_ARCH="arch-${INT_TYPE}-${REAL_TYPE}-${BUILD_TYPE}"
PETSC_DIR="$(pwd)/petsc-${PETSC_VERSION}"

echo "Host          : ${HOST}"
echo "Platform      : ${PLATFORM}"
echo "PETSc version : ${PETSC_VERSION}"
echo "Real type     : ${REAL_TYPE}"
echo "Index type    : ${INT_TYPE}"
echo "Build type    : ${BUILD_TYPE}"
echo "PETSC_DIR     : ${PETSC_DIR}"
echo "PETSC_ARCH    : ${PETSC_ARCH}"

if [[ ! -d "${PETSC_DIR}" ]]; then
  echo "Downloading PETSc ${PETSC_VERSION}..."
  curl -L "${PETSC_BASEURL}/petsc-${PETSC_VERSION}.tar.gz" | tar xz
else
  echo "Directory ${PETSC_DIR} already exists, skipping download."
fi

cd "${PETSC_DIR}"

CONFIGURE_OPTIONS=(
  "PETSC_ARCH=${PETSC_ARCH}"
  "COPTFLAGS=-O3"
  "CXXOPTFLAGS=-O3"
  "FOPTFLAGS=-O3"
  "CUDAOPTFLAGS=-O3"
  "--with-mpi=1"
  "--with-cc=mpicc"
  "--with-cxx=mpicxx"
  "--with-fc=mpif90"
  "--with-f77=mpif77"
  "--with-make-exec=gmake"
  "--with-fortran-bindings=0"
  "--with-precision=${REAL_TYPE}"
  "--with-64-bit-indices=${WITH_64_BIT_INDICES}"
  "--with-clanguage=c"
  "--download-metis"
  "--download-parmetis"
  "--download-hypre"
  "--with-debugging=${WITH_DEBUGGING}"
)

if [[ "${PLATFORM}" == "cscs" ]]; then
  CONFIGURE_OPTIONS+=(
    "--with-batch=1"
    "--with-cuda=1"
    "--with-cuda-arch=90"
    "--download-umpire"
    "--with-openblas-dir=/user-environment/env/default"
    "--known-64-bit-blas-indices=0"
    "--known-sdot-returns-double=0"
    "--known-snrm2-returns-double=0"
  )
fi

echo "Configuring PETSc..."
./configure "${CONFIGURE_OPTIONS[@]}"

echo "Building PETSc..."
make PETSC_DIR="${PETSC_DIR}" PETSC_ARCH="${PETSC_ARCH}" all

echo "Checking PETSc..."
make PETSC_DIR="${PETSC_DIR}" PETSC_ARCH="${PETSC_ARCH}" check

echo
echo "Done."
echo "Set these in your environment:"
echo "  export PETSC_DIR=\"${PETSC_DIR}\""
echo "  export PETSC_ARCH=\"${PETSC_ARCH}\""
