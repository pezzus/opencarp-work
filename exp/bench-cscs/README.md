# Coarse atrial benchmark

The active case is `benchmark_450um.par`: the 450 um patient-specific
bi-atrial mesh, a 10 ms monodomain simulation, a 50 us timestep,
consistent mass matrix, and Crank–Nicolson integration. The 225 um mesh
and its old launcher are not used.

Run from this directory:

```bash
OMP_NUM_THREADS=1 ../../src/openCARP/build_32bit/bin/openCARP \
  +F benchmark_450um.par -simID output/coarse_gpu_01 \
  + -mat_type aijcusparse -vec_type cuda -ksp_type cg -pc_type jacobi
```

Use a fresh simulation directory for each run. The existing `output`
directory is the parent. `run_petsc.sh` is only an optional convenience
wrapper for the same command; it accepts openCARP overrides, for example
`./run_petsc.sh -simID output/coarse_gpu_02`.
PETSc options after `+` belong on the direct command, not among wrapper
arguments. The mesh and output paths are relative to this directory.

All openCARP settings previously supplied by `run_petsc.sh` now live in
the parameter file. PETSc options remain separate because they configure
matrix/vector creation as well as the solver; a parabolic solver options
file alone does not replace them.

The four ionic regions, conductivities, fibrosis/scar retagging lists,
stimulus waveform and output cadence are preserved. The stimulus uses
the modern `stim[]` syntax to avoid the legacy-stimulus warning.
Ionic execution-target selection is unchanged (AUTO in the application);
GPU PETSc options do not by themselves prove which ionic target was selected.
Check the runtime log when comparing backends.

This baseline still writes Vm and an activation map. Disabling output or
activation is a separate benchmark variant, not an implicit cleanup change.
In particular, setting `num_LATs=0` alone cannot override the indexed
`lats[]` definitions in this file.

Keep `parameters_Loewe.par` as the original reference, not an additional
input to this case. Equal-looking ionic regions were deliberately not merged,
since their grouping can affect GPU execution and transfer costs.

Before performance comparisons, separate full application startup,
simulation-loop wall time and finalization. The physics `Init` timers
do not include all mesh setup, and ionic timers may not capture asynchronous
GPU completion. The initial input cleanup alone did not establish a performance
baseline; the follow-up below adds a coarse NVTX loop range and diagnostics.

## Output/activation ablation

`measure_gpu.py` runs four cases sequentially: the baseline, no field output,
no activation detection, and neither. It generates complete input snapshots
without indexed LAT definitions when activation is disabled, pins the process
to one CPU, selects GPU 0, sets one OpenMP/BLAS thread, and uses no I/O ranks.
The output-step cadence stays unchanged. Each results directory must be new:

```bash
../../.venv/bin/python measure_gpu.py --duration-ms 10 --repeats 3 --output output/gpu_ablation_new
```

Results are written incrementally to CSV/JSON alongside logs, GNU time reports,
and the actual parameter files. Startup/loop/finalization boundaries are
observed from stdout, not instrumented or GPU-synchronized phase timers.
Failed cases stop the experiment; requested repetitions may therefore be
incomplete. The initial pilot and its WSL managed-memory failure are described
below.

## Initial workstation investigation (19 October 2026)

This subsection records the historical 100 ms pilot before the managed-memory
fix. Current runs default to 10 ms. The subsequent follow-up is recorded below.

Hardware/software: RTX 4090, driver 617.42, WSL2, i9-12900KF; openCARP
`19156ca1`, LLVM 22.1.8 and PETSc 3.26.0. One rank pinned to CPU 0,
one OpenMP/BLAS thread, no I/O ranks, CUDA CG/Jacobi. The coarse mesh has
6,054,425 elements and 1,120,785 output vertices.

Results and all input snapshots are under `output/performance_20261019/`.
These are **single pilot measurements, not three-run averages**. The matrix
requested three repetitions but stopped at the first failure, before starting
the second repetition. Timings below are seconds; phase boundaries are observed
from stdout and should not be treated as exact GPU-synchronized timers.

| Case | Startup | Simulation loop | Finalization | Whole process | Physics field-output timer |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline (Vm + activation) | 31.33 | 167.90 | 0.25 | 199.48 | 0.35 |
| No field output, activation retained | 26.79 | 172.15 | 0.25 | 199.19 | 0.00 |
| No activation, field output retained | 31.48 | 167.23 | 0.14 | 198.86 | 0.37 |
| Neither | — | failed at first timestep | — | — | — |

The corresponding CG solve timers sum to 105.16, 108.22 and 106.23 seconds,
respectively. All completed cases report 2,001 solves and the same cumulative
CG iteration counts. Final Vm is finite and bitwise identical between baseline
and no-activation across all 1,120,785 output vertices.

There is no demonstrated loop-time gain from disabling either feature alone.
The nominal 0.4% activation-off difference is not a statistically established
speedup. Field output accounts for only about 0.2% of the baseline loop in
its own timer. The no-field variant shortens startup by about 4.5 seconds,
consistent with suppressing the initial mesh export.
Local cached writes and WSL behavior must not be extrapolated directly to the
CSCS filesystem or GH200 CPU/GPU interconnect.

### Managed-memory failure with neither feature enabled

The no-field/no-activation case reproducibly fails at the first timestep.
`neither_cuda_gdb.log` records:

```text
CUDA_EXCEPTION_15: Invalid Managed Memory Access
limpet::IonIfBase::get_reqdat()
limpet::MULTI_IF::getRealData(bool)
limpet::MULTI_IF::compute_ionic_current(bool, bool)
opencarp::Ionics::compute_step()
opencarp::Electrics::compute_step()
```

The device reports managed-memory support but
`cudaDevAttrConcurrentManagedAccess = 0`. A 10 ms diagnostic with
`CUDA_LAUNCH_BLOCKING=1` completes successfully. This supports a WSL
managed-memory/access-ordering problem: model interfaces live in managed memory
and CPU bookkeeping reads their fields while GPU work can be in flight.
Field output and activation can provide incidental synchronization.
The blocking run is **not** a valid performance comparison or a proposed fix.
No application source or synchronization behavior was changed during that initial pilot.

### Nsight profiling works on this WSL installation

Two short 10 ms traces (201 timesteps each) completed successfully with
Nsight Systems 2026.1.3, CUDA/NVTX tracing, CPU sampling and context-switch
tracing disabled. Reports are `baseline_nsys.nsys-rep` and
`no_activation_nsys.nsys-rep`; SQLite exports and CSV statistics are retained.
These are profiled diagnostics, separate from the unprofiled 100 ms pilot.

| Whole-trace metric (includes initialization) | Baseline | No activation |
| --- | ---: | ---: |
| CUDA runtime kernel launches | 51,074 | 51,073 |
| CUDA driver ionic kernel launches | 804 | 804 |
| CUDA runtime stream synchronizations | 11,940 | 11,907 |
| CUDA driver stream synchronizations | 804 | 804 |
| Device-to-host traffic (decimal GB) | 7.335 | 7.308 |
| Host-to-device traffic (decimal GB) | 4.397 | 4.379 |
| Full-vector D2H copies (8,966,280 bytes each) | 818 | 815 |

The baseline also has 11,844 eight-byte D2H transfers, consistent with scalar
solver reductions. GPU kernel-time totals and host API-time totals overlap;
do not add them together or interpret API percentages as application wall-time
percentages. Transfers barely change when activation is disabled. This makes
remaining host/device handoffs and solver launch/reduction overhead stronger
targets than asynchronous activation for this case.

At the time of these initial profiles, the direct PETSc/MLIR sharing path required
contiguous per-region node lists. Noncontiguous layouts fall back to host-side
gather/scatter. The follow-up below adds explicit diagnostics and indexed GPU
copies; this historical whole-trace table does not describe the new path.

### CSCS considerations and next steps

1. Resolve the managed-memory ordering failure before timing the fully
   output-free case; then repeat unprofiled measurements.
2. Inspect per-region device sharing and the remaining full-vector transfers;
   profile CG reductions/launches without adding per-timestep barriers.
3. Revisit activation separately on multiple GPUs: its current implementation
   calls an MPI all-reduce per detector per timestep, even with first-activation
   maps. A one-rank test cannot measure that scaling cost.
4. Generated MLIR CUDA modules have global constructors that call
   `mgpuModuleLoad` before `main()`. Thus even I/O-only ranks initialize CUDA
   before MPI roles are assigned. A CPU-only I/O path needs lazy module/context
   initialization or a suitable launch arrangement; MPS does not by itself
   fix a rank with no visible GPU. No extra I/O ranks were used here.

At present there is no performance evidence justifying asynchronous field
output or activation work on this workstation. This does not rule out a
benefit with a slower filesystem, higher output frequency, or multiple nodes.

## 10 ms follow-up: ordering, transfers and correctness

The managed-memory crash is addressed with capability-gated synchronization
before CPU access to managed model metadata. This WSL device reports no
concurrent managed access. `CUDA_LAUNCH_BLOCKING` is not used for measurements.
All four ionic regions select `mlir-cuda`, with noncontiguous node lists of
14,007, 616,292, 480,535 and 9,951 entries. Their ODE ordering is preserved.
Device-only gather/scatter buffers and cached index maps connect that ordering
to PETSc's global-vector order. `OPENCARP_CUDA_INDEXED_DATA=0` selects the old
host mapping for A/B tests without disabling contiguous aliases.

Before indexed mapping/native vector arithmetic, three unprofiled repetitions
of each 10 ms case completed (`output/gpu_10ms_fixed_20261019/`):

| Case | Loop mean (s) | Sample SD (s) | Detector evaluations per run |
| --- | ---: | ---: | ---: |
| Baseline | 15.431 | 0.856 | 201 |
| No field output | 15.164 | 0.034 | 201 |
| No activation | 14.897 | 0.190 | 0 |
| Neither | 15.497 | 0.099 | 0 |

The zero-detector cases also report APD=0 and sentinel=0. The check wrapper is
still called 201 times but executes zero detector evaluations; activation is
not secretly active. These results do not show an output/activation bottleneck
on this one-rank workstation.

Indexed mapping alone produced bitwise-identical Vm frames, but barely changed
the loop time or explicit transfers. Further inspection found that the PETSc
wrapper performed scaling, pointwise multiplication, AXPY, shifting and vector
assignment through CPU array access. These now dispatch through native PETSc
operations, with a host-copy fallback for assignment from a different backend.

Matched Nsight Systems measurements below include only the NVTX simulation-loop
range, with fields and activation disabled (201 timesteps):

| Metric | Host mapping + old arithmetic | GPU mapping + old arithmetic | GPU mapping + native arithmetic |
| --- | ---: | ---: | ---: |
| Loop wall time (s) | 15.627 | 15.706 | 12.586 |
| D2H traffic (decimal GB) | 7.209 | 7.218 | 0.726 |
| H2D traffic (decimal GB) | 3.797 | 3.810 | 0.923 |
| Full-vector D2H copies | 804 | 805 | 81 |
| Full-vector H2D copies | 403 | 404 | 82 |
| Kernel-active coverage of loop | 26.0% | 25.2% | 29.5% |

The remaining 81 full-vector round trips correspond to the active 0--4 ms
stimulus: `apply_stim_to_vector()` still calls the host-backed indexed `set()`.
H2D also includes the first-use matrix/index-map uploads. About 11,900 eight-byte
D2H copies remain, mostly scalar solver results. The trace has about 55,900
kernels; sparse matrix-vector products and norms take more GPU time than the
ionic kernels. Kernel/API timings overlap and must not be added together.
Explicit-copy totals do not include any untraced managed-memory migration.

With indexed mapping alone, saved Vm files are bitwise identical. After native
PETSc arithmetic, comparison of all 11 saved frames against the old path gives
maximum absolute Vm difference 6.1035e-5 mV, RMS difference 1.6762e-7 mV, and no
nonfinite values. Arithmetic order/rounding changes slightly; the CG iteration
total changes from 3,680 to 3,693 at the same tolerance. This is a short-run
regression check, not a long-duration electrophysiology validation.

Three final unprofiled repetitions per case, after both GPU mapping and native
arithmetic (`output/gpu_native_10ms_20261019/`):

| Case | Loop mean +/- sample SD (s) | Reduction vs old path | Startup mean (s) | Whole-process mean (s) |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 12.935 +/- 0.103 | 16.2% | 34.927 | 48.168 |
| Neither | 12.103 +/- 0.125 | 21.9% | 28.489 | 40.756 |

All six runs complete; each activation-off run has zero detector evaluations.
All three baseline Vm files have the same maximum difference quoted above.
The combined field/activation ablation is now about 6.4% faster in the loop;
this does not isolate activation from field output and does not by itself
justify a particular asynchronous implementation. Baseline startup includes
one-off mesh output and is substantially longer; do not extrapolate that cost
by multiplying it by simulated duration. Physics `Init` is not the complete
application startup time.

Raw reports and input snapshots are in `output/gpu_mapping_smoke_20261019/`
and `output/native_vector_smoke_20261019/`. `analyze_gpu_trace.py` extracts
loop-only activity and transfers from an Nsight SQLite export. Its activity
coverage is not SM utilization or a fraction of peak FLOP/s/bandwidth.

### Performance counters

Nsight Compute counters work after enabling them on the Windows host.
The isolated Courtemanche probe reaches about 25% occupancy, limited by
register usage, with FP64 its highest-utilized compute pipeline. Coupled-case
samples show the CSR matrix-vector kernel is bandwidth dominated, while the
ionic kernel is constrained by FP64/memory dependencies and registers.
However, the replayed coupled samples report DRAM clocks around 0.81 GHz
instead of 10.24 GHz in the isolated probe, and reduced SM clocks. Their
throughput percentages cannot establish proximity to the card's normal-clock
peak, nor be extrapolated to GH200. The Systems trace more reliably shows
that the application as a whole still spends most of its loop outside traced
GPU kernels. Solver scalar synchronization/CPU launch overhead and the active
stimulus host path are better next targets than asynchronous disk output.

### Regression-test limitations

The new PETSc arithmetic test passes with ordinary CPU vectors, CUDA vectors,
and two MPI ranks with CPU vectors. Parser, mesh-I/O, numbering and PETSc
options tests pass. CUDA cloning originally used CPU `memcpy` on device state;
that copy now uses CUDA with explicit completion, and the static/tuned clone
tests pass. The full CUDA-default suite is **not clean**: state saving
(`limpet-doppel-external-vm`), cell-geometry cloning, plugin-tree and RRC tests
still fail; the Lapicque probe also failed/timed out in the earlier run and
was excluded from the latest subset. These standalone `bench` paths require
separate investigation. The atrial comparison does not exercise checkpoint
state saving, those plugins or clone/reset protocols. Logs are retained in
`output/native_vector_smoke_20261019/ctest*.log`; do not interpret the
successful atrial test as validation of every CUDA workflow.
All eight clone/state test cases pass when explicitly selecting the CPU target
(`cpu_clone_regressions.log`), including the state-I/O, plugin, RRC and Lapicque
cases that fail or time out under the CUDA-default test invocation.
