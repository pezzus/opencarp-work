#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./bench_mem.sh "PETSC options"
# Example:
#   ./bench_mem.sh "-ksp_type cg -pc_type gamg"

GMESH="${GMSH:-/Applications/Gmsh.app/Contents/MacOS/gmsh}"
OPENCARP="${OC64_BIN:-../src/openCARP/build_64bit/bin/openCARP}"
NP="${NP:-4}"
PETSC_OPTS="${1:-}"

SCRIPT_DIR="${OC_SCRIPTS:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PARSE_SCRIPT="${SCRIPT_DIR}/parse_petsc_mem.py"

OUTDIR="${OUTDIR:-bench_logs}"
CSV="${OUTDIR}/memory_results.csv"
mkdir -p "$OUTDIR"

if [[ "$OPENCARP" == *"64"* ]]; then
    BINARY="64-bit"
elif [[ "$OPENCARP" == *"32"* ]]; then
    BINARY="32-bit"
else
    BINARY="unknown"
fi

: > "$CSV"
echo "# NP=${NP}, BINARY=${BINARY}, PETSC_OPTS='${PETSC_OPTS}', OPENCARP='${OPENCARP}'" >> "$CSV"

run_case() {
    local nx="$1"
    local ny="$2"
    local nz="$3"

    local tag="nx${nx}_ny${ny}_nz${nz}"
    local log="${OUTDIR}/${tag}.log"
    local time_log="${OUTDIR}/${tag}.time"

    echo "=== ${tag} ==="

    "${GMESH}" ../lego/emigrid.geo \
        -o emigrid.msh \
        -setnumber nx "${nx}" \
        -setnumber ny "${ny}" \
        -setnumber nz "${nz}" \
        -3 > "${OUTDIR}/${tag}.gmsh.log" 2>&1

    uv run ../../scripts/convert_mesh.py emigrid.msh > "${OUTDIR}/${tag}.convert.log" 2>&1

    # shellcheck disable=SC2086
    /usr/bin/time -l \
        mpirun -np "${NP}" "${OPENCARP}" \
        +F emigrid.par \
        -tend 1.0 \
        + ${PETSC_OPTS} \
        -memory_view \
        -log_view \
        -log_view_memory \
        > "${log}" 2> "${time_log}"

    uv run python "${PARSE_SCRIPT}" \
        --nx "${nx}" --ny "${ny}" --nz "${nz}" \
        --petsc-log "${log}" \
        --time-log "${time_log}" \
        --csv "${CSV}"
}

for nx in 2 4 8 16; do
    run_case "${nx}" 1 1
done

echo
echo "Results written to ${CSV}"
