#!/usr/bin/env bash
set -euo pipefail

PETSC_BASEURL="https://web.cels.anl.gov/projects/petsc/download/release-snapshots"

usage() {
  cat <<EOF
Usage:
  $0 [version] [real] [int] [build]

Arguments:
  version   PETSc version                 default: 3.26.0
  real      single | double               default: double
  int       32 | 64                       default: 32
  build     opt | dbg                     default: opt

Examples:
  $0
  $0 3.26.0
  $0 3.26.0 double 64 dbg
  $0 3.25.6 single 32 opt

Environment:
  PETSC_BUILD_JOBS   Override the number of parallel build jobs
  PETSC_CUDA_ARCH    CUDA compute capability (default: 89 locally, 90 on CSCS)
  PETSC_CUDAC        Path to nvcc (default: nvcc found in PATH)
  PETSC_CUDA_DRIVER_LIB_DIR
                     Directory containing libcuda.so when it is not in the
                     compiler's default link path (auto-detected on WSL)
EOF
}

PETSC_VERSION="${1:-3.26.0}"
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

if [[ "${PLATFORM}" == "cscs" ]]; then
  CUDA_ARCH="${PETSC_CUDA_ARCH:-90}"
else
  CUDA_ARCH="${PETSC_CUDA_ARCH:-89}"
fi

if [[ -n "${PETSC_CUDAC:-}" ]]; then
  CUDA_COMPILER="${PETSC_CUDAC}"
elif command -v nvcc >/dev/null 2>&1; then
  CUDA_COMPILER="$(command -v nvcc)"
elif [[ -n "${CUDA_HOME:-}" && -x "${CUDA_HOME}/bin/nvcc" ]]; then
  CUDA_COMPILER="${CUDA_HOME}/bin/nvcc"
else
  echo "Error: nvcc was not found." >&2
  echo "Load CUDA, set CUDA_HOME, or set PETSC_CUDAC=/path/to/nvcc." >&2
  exit 1
fi

if [[ ! -x "${CUDA_COMPILER}" ]]; then
  echo "Error: CUDA compiler is not executable: ${CUDA_COMPILER}" >&2
  exit 1
fi

# WSL exposes the real NVIDIA driver library outside GCC's default link path.
# MPICH's CUDA probe links both libcudart and libcuda; without this path it
# silently disables CUDA in MPL even though its UCX and Yaksa builds find CUDA.
CUDA_DRIVER_LIB_DIR="${PETSC_CUDA_DRIVER_LIB_DIR:-}"

if [[ -z "${CUDA_DRIVER_LIB_DIR}" && -f /usr/lib/wsl/lib/libcuda.so ]]; then
  CUDA_DRIVER_LIB_DIR="/usr/lib/wsl/lib"
fi

if [[ -n "${CUDA_DRIVER_LIB_DIR}" && ! -f "${CUDA_DRIVER_LIB_DIR}/libcuda.so" ]]; then
  echo "Error: libcuda.so was not found in ${CUDA_DRIVER_LIB_DIR}." >&2
  exit 1
fi

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
echo "CUDA compiler : ${CUDA_COMPILER}"
echo "CUDA arch     : ${CUDA_ARCH}"
echo "CUDA driver   : ${CUDA_DRIVER_LIB_DIR:-system linker path}"
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
  "CUDAOPTFLAGS=-O3"
  "--with-mpi=1"
  "--download-mpich"
  "--with-cc=gcc"
  "--with-cxx=g++"
  "--with-fc=0"
  "--with-make-exec=gmake"
  "--with-fortran-bindings=0"
  "--with-precision=${REAL_TYPE}"
  "--with-64-bit-indices=${WITH_64_BIT_INDICES}"
  "--with-clanguage=c"
  "--download-metis"
  "--download-parmetis"
  "--download-hypre"
  "--with-debugging=${WITH_DEBUGGING}"
  "--with-cuda=1"
  "--with-cudac=${CUDA_COMPILER}"
  "--with-cuda-arch=${CUDA_ARCH}"
  "--download-umpire"
)

if [[ -n "${CUDA_DRIVER_LIB_DIR}" ]]; then
  CONFIGURE_OPTIONS+=(
    "--download-mpich-configure-arguments=LDFLAGS=-L${CUDA_DRIVER_LIB_DIR}"
  )
fi

if [[ "${PLATFORM}" == "cscs" ]]; then
  CONFIGURE_OPTIONS+=(
    "--with-batch=1"
    "--with-openblas-dir=/user-environment/env/default"
    "--known-64-bit-blas-indices=0"
    "--known-sdot-returns-double=0"
    "--known-snrm2-returns-double=0"
  )
fi

echo "Configuring PETSc..."
./configure "${CONFIGURE_OPTIONS[@]}"

# PETSc can recognize CUDA symbols in MPICH's Yaksa library even when MPICH's
# own MPL CUDA probe failed. Catch that partial build before compiling PETSc.
MPICH_MPL_CONFIG="$(find "${PETSC_DIR}/${PETSC_ARCH}/externalpackages" \
  -path '*/src/mpl/include/mplconfig.h' -print -quit 2>/dev/null || true)"

if [[ -n "${MPICH_MPL_CONFIG}" ]] && \
   ! grep -F '#define MPL_HAVE_CUDA 1' "${MPICH_MPL_CONFIG}" >/dev/null; then
  echo "Error: the downloaded MPICH was built without MPL CUDA support." >&2
  echo "Check its MPL config.log for a failed '-lcuda' link test." >&2
  echo "If needed, set PETSC_CUDA_DRIVER_LIB_DIR to the directory containing libcuda.so." >&2
  exit 1
fi

# Reconfiguring an existing CUDA architecture after enabling Umpire can leave
# HYPRE object files compiled with the previous feature set. Its installed
# headers then advertise the Umpire API while libHYPRE does not export it.
# Rebuild only HYPRE when that precise inconsistency is detected.
HYPRE_LIBRARY="${PETSC_DIR}/${PETSC_ARCH}/lib/libHYPRE.so"
HYPRE_SOURCE="${PETSC_DIR}/${PETSC_ARCH}/externalpackages/git.hypre/src"
HYPRE_UMPIRE_SYMBOL="HYPRE_SetUmpireDevicePoolSize"

if [[ -f "${HYPRE_LIBRARY}" ]] && \
   ! nm -D --defined-only "${HYPRE_LIBRARY}" | grep -F "${HYPRE_UMPIRE_SYMBOL}" >/dev/null; then
  if [[ ! -d "${HYPRE_SOURCE}" ]]; then
    echo "Error: ${HYPRE_LIBRARY} lacks ${HYPRE_UMPIRE_SYMBOL}," >&2
    echo "but the downloaded HYPRE source was not found at ${HYPRE_SOURCE}." >&2
    exit 1
  fi

  echo "Rebuilding HYPRE to activate Umpire support..."
  gmake -C "${HYPRE_SOURCE}" clean
  gmake -C "${HYPRE_SOURCE}" -j "${BUILD_JOBS}"
  gmake -C "${HYPRE_SOURCE}" install

  if ! nm -D --defined-only "${HYPRE_LIBRARY}" | grep -F "${HYPRE_UMPIRE_SYMBOL}" >/dev/null; then
    echo "Error: rebuilt HYPRE still lacks ${HYPRE_UMPIRE_SYMBOL}." >&2
    exit 1
  fi
fi

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
