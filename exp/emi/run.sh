#!/usr/bin/env bash
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-node=4
#SBATCH --cpus-per-task=1
#SBATCH --hint=nomultithread
#SBATCH --partition=debug
#SBATCH --account=lp157
#SBATCH --uenv=prgenv-gnu/26.3:v1
#SBATCH --output=output/slurm-%j.out

# GPUs are allocated per node so CPU runs can request 144 MPI ranks/node
# without requesting 144 GPUs. CPU jobs leave these GPUs unused.
set -euo pipefail
die() { echo "Error: $*" >&2; exit 2; }
usage() {
  echo "Usage: run.sh [--dry-run] PETSC.opt [openCARP options ...] [+ PETSc overrides ...]"
  echo "Submit from exp/emi, or set EMI_WORKDIR. See BENCHMARKING.txt."
}
dry_run=0
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
if [[ ${1:-} == --dry-run ]]; then dry_run=1; shift; fi
[[ $# -ge 1 ]] || { usage >&2; exit 2; }
launcher_source=$(realpath -e -- "${BASH_SOURCE[0]}")
cd -- "${EMI_WORKDIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
workdir=$PWD
opts=$(realpath -e -- "$1") || die "Cannot read options file: $1"
shift
[[ -f $opts && -r $opts ]] || die "Not a readable options file: $opts"
backend=${EMI_BACKEND:-$(awk '/^#[[:space:]]*emi_backend:/ {print $3}' "$opts")}
[[ $backend == cpu || $backend == gpu ]] || die "Set '# emi_backend: cpu|gpu' in the options file, or EMI_BACKEND."
app_args=()
petsc_args=()
in_petsc=0
for arg in "$@"; do
  if [[ $arg == + ]]; then
    (( in_petsc == 0 )) || die "Only one '+' separator is allowed."
    in_petsc=1
  elif (( in_petsc )); then
    petsc_args+=("$arg")
  else
    case $arg in
      -simID|-parab_options_file|-flavor)
        die "$arg is managed by this launcher; use EMI_RUN_DIR for the output path." ;;
    esac
    app_args+=("$arg")
  fi
done
if (( ! dry_run )); then
  [[ -n ${SLURM_JOB_ID:-} ]] || die "Submit with sbatch. Local execution is disabled; --dry-run only prints the command."
  [[ -n ${SLURM_NTASKS:-} ]] || die "SLURM_NTASKS is missing. Request --ntasks-per-node."
fi
nodes=${SLURM_JOB_NUM_NODES:-${SLURM_NNODES:-1}}
[[ $nodes =~ ^[1-9][0-9]*$ ]] || die "Node count must be a positive integer."
if [[ $backend == cpu ]]; then default_tasks=$((144 * nodes)); else default_tasks=$((4 * nodes)); fi
tasks=${SLURM_NTASKS:-$default_tasks}
threads=${SLURM_CPUS_PER_TASK:-1}
for value in "$tasks" "$threads"; do
  [[ $value =~ ^[1-9][0-9]*$ ]] || die "Task/CPU counts must be positive integers."
done
workspace=$(realpath -e -- "$workdir/../..")
if [[ $backend == cpu ]]; then
  [[ -n ${OPENCARP_CPU_BIN:-} ]] || die "CPU presets require OPENCARP_CPU_BIN: a CPU-only openCARP executable (CPU PETSc and no GPU ionic targets). See BENCHMARKING.txt."
  binary=$OPENCARP_CPU_BIN
  # This fails fast for a mistakenly supplied CUDA-only executable. It does
  # not switch ionic models to their CPU implementation.
  export CUDA_VISIBLE_DEVICES=""
  # srun may reset GPU visibility for the step. Override it again in each
  # task, after Slurm has installed that task's environment.
  app_prefix=(env CUDA_VISIBLE_DEVICES=)
  matvec=(-mat_type aij -vec_type standard)
  gpu_step=()
else
  binary=${OPENCARP_BIN:-${OPENCARP:-$workspace/src/openCARP/build_32bit/bin/openCARP}}
  app_prefix=()
  (( tasks <= 4 * nodes )) || die "GPU mode supports at most 4 ranks/node on Daint; CPU presets support higher rank counts."
  matvec=(-mat_type aijcusparse -vec_type cuda)
  gpu_step=(--gpus-per-task=1 --gpu-bind=closest)
fi
if (( ! dry_run )); then [[ -x $binary ]] || die "Executable not found: $binary"; fi
binary=$(realpath -m -- "$binary")
par=$(realpath -e -- "${EMI_PARAM_FILE:-rmtest-10x10x10-trim-veri.par}") || die "Cannot read EMI_PARAM_FILE."
[[ -r $par ]] || die "Parameter file is not readable: $par"
setup=$(basename -- "${opts%.opt}")
setup=${setup//[^[:alnum:]_-]/_}
run_dir=$(realpath -m -- "${EMI_RUN_DIR:-output/bench_job_${SLURM_JOB_ID:-DRYRUN}${SLURM_ARRAY_TASK_ID:+_${SLURM_ARRAY_TASK_ID}}_${setup}}")
metadata="${run_dir}.metadata"
[[ $metadata != *[[:space:]]* && $opts != *[[:space:]]* ]] || die "Benchmark output/options paths must not contain whitespace (PETSc option serialization)."
[[ ! -e $run_dir && ! -e $metadata ]] || die "Output already exists; choose another EMI_RUN_DIR: $run_dir"
export OMP_NUM_THREADS=$threads
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export BLIS_NUM_THREADS=1
cmd=(srun --nodes="$nodes" --ntasks="$tasks" --cpus-per-task="$threads"
     --cpu-bind="${EMI_CPU_BIND:-cores}" --distribution="${EMI_DISTRIBUTION:-block:cyclic}"
     "${gpu_step[@]}" "${app_prefix[@]}" "$binary" +F "$par"
     "${app_args[@]}" -flavor petsc -parab_options_file "$metadata/solver.opt" -simID "$run_dir"
     + "${matvec[@]}" -log_view ":$metadata/petsc.log" "${petsc_args[@]}")
echo "Setup: $setup; backend: $backend; nodes: $nodes; MPI ranks: $tasks; threads/rank: $threads"
echo "Working directory: $workdir"
echo "Command:"
printf '%q ' "${cmd[@]}"
printf '\n'
if (( dry_run )); then exit 0; fi
mkdir -p -- "$(dirname -- "$metadata")"
mkdir -- "$metadata"
cp -- "$opts" "$metadata/solver.opt"
cp -- "$par" "$metadata/input.par"
cp -- "$launcher_source" "$metadata/run.sh"
printf '%q ' "${cmd[@]}" > "$metadata/command.txt"
printf '\n' >> "$metadata/command.txt"
for key in SLURM_JOB_ID SLURM_JOB_NODELIST SLURM_JOB_NUM_NODES SLURM_NTASKS \
           SLURM_TASKS_PER_NODE SLURM_CPUS_PER_TASK SLURM_GPUS_PER_NODE \
           OMP_NUM_THREADS OMP_PLACES OMP_PROC_BIND OPENBLAS_NUM_THREADS \
           MKL_NUM_THREADS BLIS_NUM_THREADS CUDA_VISIBLE_DEVICES \
           MPICH_GPU_SUPPORT_ENABLED MPICH_OFI_NIC_POLICY FI_CXI_RX_MATCH_MODE \
           MPICH_ASYNC_PROGRESS UCX_TLS PETSC_OPTIONS LD_PRELOAD LD_LIBRARY_PATH \
           OPENCARP_CUDA_INDEXED_DATA; do
  printf '%s=%s\n' "$key" "${!key-<unset>}" >> "$metadata/environment.txt"
done
echo "Started: $(date -Is)" | tee "$metadata/status.txt"
set +e
"${cmd[@]}"
result=$?
set -e
echo "Finished: $(date -Is); exit code: $result" | tee -a "$metadata/status.txt"
exit "$result"
