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

Environment:
  PETSC_BUILD_JOBS   Override the number of parallel build jobs
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

# ---------------------------------------------------------------------------
# Build configuration
# ---------------------------------------------------------------------------

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

# nproc respects CPU affinity on Linux, including Slurm restrictions.
# Allow an explicit override for large builds.
BUILD_JOBS="${PETSC_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}"

if command -v nproc >/dev/null 2>&1; then
  BUILD_JOBS="${PETSC_BUILD_JOBS:-$(nproc)}"
fi

echo "Host          : ${HOST}"
echo "Platform      : ${PLATFORM}"
echo "PETSc version : ${PETSC_VERSION}"
echo "Real type     : ${REAL_TYPE}"
echo "Index type    : ${INT_TYPE}"
echo "Build type    : ${BUILD_TYPE}"
echo "Build jobs    : ${BUILD_JOBS}"
echo "PETSC_DIR     : ${PETSC_DIR}"
echo "PETSC_ARCH    : ${PETSC_ARCH}"

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

if [[ ! -d "${PETSC_DIR}" ]]; then
  echo "Downloading PETSc ${PETSC_VERSION}..."

  curl -fL \
    "${PETSC_BASEURL}/petsc-${PETSC_VERSION}.tar.gz" \
    | tar -xz -C "$(pwd)"
else
  echo "Directory ${PETSC_DIR} already exists, skipping download."
fi

cd "${PETSC_DIR}"

# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------

CONFIGURE_OPTIONS=(
  "PETSC_ARCH=${PETSC_ARCH}"
  "COPTFLAGS=-O3"
  "CXXOPTFLAGS=-O3"
  "FOPTFLAGS=-O3"
  "CUDAOPTFLAGS=-O3"
  "--with-mpi=1"
  "--with-cc=mpicc"
  "--with-cxx=mpicxx"
  "--with-fc=mpifort"
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

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

echo "Building PETSc (${BUILD_JOBS} jobs)..."

make \
  PETSC_DIR="${PETSC_DIR}" \
  PETSC_ARCH="${PETSC_ARCH}" \
  -j "${BUILD_JOBS}" \
  all

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

if [[ "${PLATFORM}" == "cscs" ]]; then
  echo "Skipping PETSc checks (batch configuration)."
else
  echo "Checking PETSc..."

  make \
    PETSC_DIR="${PETSC_DIR}" \
    PETSC_ARCH="${PETSC_ARCH}" \
    check
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
echo "Done."
echo "Set these in your environment:"
echo "  export PETSC_DIR=\"${PETSC_DIR}\""
echo "  export PETSC_ARCH=\"${PETSC_ARCH}\""
