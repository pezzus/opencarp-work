#!/bin/bash
#SBATCH --time=00:10:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-task=1
#SBATCH --hint=nomultithread
#SBATCH --partition=debug
#SBATCH --account=lp157
#SBATCH --uenv=prgenv-gnu/26.3:v1

export OMP_NUM_THREADS=1
set -euo pipefail

echo -e "$SLURM_JOB_NAME started on $(date)\n"
echo -e "nodes: $SLURM_JOB_NUM_NODES"
echo -e "tasks: $SLURM_NTASKS"

#-num_io_nodes 1 \

OPENCARP=$HOME/ritom/opencarp-work/src/openCARP/build_32bit/bin/openCARP

srun ./jobreport -- $OPENCARP \
  +F atrial/parameters_Loewe.par \
  -simID atrial/bench_job_$SLURM_JOB_ID \
  -mass_lumping 0 \
  -meshname atrial/mesh/P1_225um_spl \
  -output_level 1 \
  -pstrat 2 \
  -cg_tol_parab 0.0001 \
  -gridout_i 2 \
  -dataout_i 2 \
  -gridout_e 0 \
  -dataout_e 0 \
  -numtagreg 4 \
  -dt 50.0 \
  -bidomain 0 \
  -parab_solve 1 \
  -num_stim 1 \
  -stimulus[0].stimtype 0 \
  -stimulus[0].start 0.0 \
  -stimulus[0].strength 70 \
  -stimulus[0].duration 4.0 \
  -stimulus[0].geometry 37 \
  -num_LATs 1 \
  -lats[0].all 0 \
  -lats[0].measurand 0 \
  -lats[0].mode 0 \
  -lats[0].threshold -50 \
  -spacedt 1 \
  -vofile vm_last_beat \
  -timedt 1.0 \
  -tend 100.0 \
  + -mat_type aijcusparse -vec_type cuda -ksp_type cg -pc_type jacobi

echo -e "$SLURM_JOB_NAME finished on $(date)\n"

