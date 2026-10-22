#!/usr/bin/env bash
# Optional convenience wrapper; all openCARP settings live in benchmark_450um.par.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
OPENCARP="${OPENCARP:-../../src/openCARP/build_32bit/bin/openCARP}"

exec "$OPENCARP" +F benchmark_450um.par "$@" \
  + -mat_type aijcusparse -vec_type cuda -ksp_type cg -pc_type jacobi

