# openCARP 64-bit / EMI / PETSc Handoff

Context for resuming work in a fresh Codex session or a new checkout.

## Repository State

- Main repo used here: `/Users/pezzuto/Desktop/opencarp-work/src/openCARP`
- Active branch: `issue-461-petsc-64bit-indices`
- Merge request: https://git.opencarp.org/openCARP/openCARP/-/merge_requests/444
- Branch was rebased onto `origin/master` and force-pushed after the last EMI memory commits.
- Last pushed commit at handoff: `3bc6a7e7 Reduce redundant EMI mesh setup memory`

Useful state check:

```bash
cd /Users/pezzuto/Desktop/opencarp-work/src/openCARP
git status --short
git log --oneline -12
```

## Main Work Completed

### 64-bit index support

- Added compile-time type policy for:
  - local index width
  - global index width
  - real precision
- Default remains 32-bit.
- PETSc consistency checks verify that configured openCARP index/real types match the PETSc build where required.
- Added `opencarp::local_index_t` / `opencarp::global_index_t` style policy and propagated through PETSc/Ginkgo-facing code.
- Fixed legacy Makefile and MLIR/CUDA paths so generated `opencarp_types.h` is available outside standard CMake-only builds.
- Added `show_build_info` type information for debugging builds on HPC.

### PETSc options and errors

- Centralized PETSc error handling in `numerics/petsc/petsc_utils.*`.
- Added compact `PETSC_CHECK(...)` style usage.
- Fixed PETSc options-file cleanup for 64-bit PETSc by avoiding fragile PETSc C-equivalent parser behavior that broke in MPI/64-bit cases.
- Options cleanup is now collective and preserves legacy openCARP comment-only markers `#`, `!`, and `%`.
- Added a small PETSc options cleanup test.
- Restored user-preferred PETSc options behavior after a temporary command-line precedence experiment:
  - defaults
  - options file
  - command-line options after `+`
- Fixed `-options_left` false positives for Mat/Vec options that were actually consumed during object creation.

### PETSc CUDA / Mat/Vec options

- Ensured matrix/vector creation calls `SetFromOptions` where needed so options such as:

```text
-mat_type aijcusparse -vec_type cuda -use_gpu_aware_mpi
```

can take effect.
- Observed PETSc/GPU behavior:
  - `-log_view_gpu_time` slows the run and should not be used for production benchmarks.
  - For the reported 4 GPU case, PETSc log showed solver/preconditioner dominated runtime, with heavy `VecScatter`/`MatMult` communication.
  - `hypre` may require device vectors consistently; mixed CPU/device vectors trigger `HYPRE_MEMORY_DEVICE expects a device vector`.

### Ginkgo

- Introduced compile-time index policy for Ginkgo.
- Default remains 32-bit.
- Ginkgo was confirmed compiling after Ginkgo/SUNDIALS were installed locally.
- Ginkgo worked in the EMI test case where one PETSc preallocation heuristic temporarily failed.

### LIMPET and MLIR

- Propagated configured local index type through LIMPET mesh-sized paths:
  - node counts
  - node lists
  - checkpoint canonical node ordering
  - code generation paths
- Regenerated LIMPET model sources in a separate commit.
- Updated dynamic model build path on Cray from C++14 to C++17 to match main build requirements.
- Added CMake-driven local-index-bit input to MLIR code generation rather than hard-coding 8-byte padding.

### MPI datatype handling

- Centralized MPI datatype handling for configured openCARP index types.
- Avoided replacing old `int` coupling with `long long` coupling in communication-sensitive paths.
- Reviewer concern addressed: deterministic EMI numbering and related MPI paths should use configured mesh/index types plus the central MPI datatype helper.

### EMI correctness fixes

- Found a large-mesh parallel 64-bit bug caused by invalid face/element lookup due to type/silent casting issues in EMI mesh handling.
- Fixed the relevant EMI mesh/mapping path so:
  - 64-bit serial worked
  - 64-bit parallel large mesh no longer produced NaN/zero-volume assembly from invalid coordinates
  - small `emi/tenCells` regression had not caught it because the mesh/index ranges were too small
- Added partition-file safeguard context:
  - stale `.part` files generated for different MPI sizes can produce misleading behavior
  - a `.part` file for 12 partitions was accidentally used with 1 task, causing stimulus/mesh confusion

### EMI output and timings

- Added `gridout_emi` control to skip writing huge EMI domain meshes for benchmarks.
- EMI mesh output now follows input mesh format:
  - binary input writes binary output
  - avoids giant text `_e.{elem,pts}` output when possible
- Added verbose EMI initialization subtimings under higher output level.
- Useful when diagnosing large meshes where “EMI Init” is too coarse.

### EMI memory work

Goal: reduce memory peak during EMI init/assembly without broad refactor.

Completed:

- Reduced redundant EMI mesh setup memory.
- Reduced EMI init memory peak.
- Reduced EMI assembly memory overhead.
- Preserved conservative PETSc preallocation for direct unique/both-face operators after a narrower heuristic caused PETSc allocation errors.

Important line intentionally preserved:

```cpp
operator_unique_to_both_faces->init(..., max_row_entries_emi*10);
operator_both_to_unique_face->init(..., max_row_entries_emi*10);
```

Do not lower these again without a better preallocation proof/test, otherwise PETSc can abort with:

```text
New nonzero at (...) caused a malloc.
Use MatSetOption(A, MAT_NEW_NONZERO_ALLOCATION_ERR, PETSC_FALSE) to turn off this check
```

Removed/avoided:

- Broad coordinate-buffer rewrites that were more invasive and riskier.
- Non-essential EMI debug probes in production path.

## Verification Performed

### Memory benchmark

Script:

```bash
cd /Users/pezzuto/Desktop/opencarp-work/exp/bench_mem
../../scripts/bench_mem.sh
```

Latest successful post-rebase results:

```text
nx2:  PETSc malloc/dof 17.69 KB, proc/dof 57.30 KB, RSS/dof 15.63 KB
nx4:  PETSc malloc/dof 15.96 KB, proc/dof 36.64 KB, RSS/dof  9.67 KB
nx8:  PETSc malloc/dof 15.77 KB, proc/dof 27.92 KB, RSS/dof  7.25 KB
nx16: PETSc malloc/dof 15.67 KB, proc/dof 23.55 KB, RSS/dof  6.04 KB
```

CSV output:

```text
/Users/pezzuto/Desktop/opencarp-work/exp/bench_mem/bench_logs/memory_results.csv
```

Earlier before/after reference from this work:

```text
Before:
  EMI dofs:              78948
  PETSc malloc / dof:    32.09 KB/dof
  PETSc proc mem / dof:  38.59 KB/dof
  RSS / dof:             9.76 KB/dof

After intermediate:
  PETSc malloc / dof:    19.52 KB/dof
  PETSc proc mem / dof:  26.14 KB/dof
  RSS / dof:             6.67 KB/dof

After latest local benchmark trend:
  PETSc malloc / dof:    ~15.7 KB/dof for nx8/nx16
  RSS / dof:             ~6-7 KB/dof for larger local cases
```

### Build / CI

- GitLab CI was green after:
  - 64-bit PETSc build job
  - 64-bit PETSc autotester job added by duplicating the CPU PETSc autotester with the 64-bit image
  - MLIR/NVIDIA Docker issue fixed by ensuring generated type header is available in that build path
- Last local `build_64bit` rebuild after rebase failed before compiling touched code because the local Python environment could not import `mlir.ir`.
- This was treated as local environment/tooling issue, not source breakage:

```text
ModuleNotFoundError: No module named 'mlir'
```

If rebuilding locally, either fix the Python MLIR module path/env or configure without MLIR codegen.

## Known Caveats

- Submodule pointers may appear dirty locally after fetch/rebase. Do not commit them unless intentionally updating submodules.
- PETSc 3.25.1 was used in local and CI dependency work. There was a question whether requiring 3.25.1 is too recent; current Docker deps were aligned with CI at the time.
- PETSc CUDA scaling remains open:
  - 2 GPUs faster than 4 GPUs in the reported case
  - PETSc log suggests solver communication/preconditioner dominates
  - need compare CPU/GPU, 1/2/4 ranks, with and without I/O and without `-log_view_gpu_time`
- `hypre` on CUDA needs fully consistent device vector/matrix setup; mixed paths fail.
- Very large EMI meshes may still need deeper architecture changes:
  - subset-of-ranks mesh input/partition/redistribution, similar to propag5
  - better partitioner integration
  - further matrix preallocation analysis
  - avoiding repeated global broadcasts in mesh insert/partition setup

## Reviewer Points Addressed

- Merge conflicts resolved by rebase onto master.
- MPI datatype handling centralized.
- PETSc options-file cleanup made collective and tested.
- LIMPET mesh-sized `int` paths broadened to configured index policy.
- 32-bit vs 64-bit numerical matching still needs explicit documented benchmark evidence for the MR discussion.
- CI now has 64-bit PETSc build and autotester coverage.
- `SF_init.h` vector factory size arguments fixed to preserve `SF_int` sizes rather than narrowing through `int`.
- Dynamic Cray model build path updated to C++17.
- Options cleanup parser preserves legacy `#`, `!`, `%` comments.

## Points Still To Address

### Before merge

- Confirm latest GitLab pipeline after final push is green.
- Add or report explicit 32-bit vs 64-bit numerical comparison:
  - same mesh/problem
  - same PETSc options where possible
  - compare solution/LAT/output tolerances
  - document expected numerical jitter
- Decide whether to document PETSc options-file comment conventions (`#`, `!`, `%`) in user docs or developer docs.
- Confirm dependency version policy:
  - whether PETSc 3.25.1 is acceptable as CI/default dependency
  - whether MR should state “tested with 3.25.1” rather than “requires 3.25.1”

### EMI memory follow-up

- Keep current memory changes limited; avoid broad refactor in this MR unless failures appear.
- Investigate remaining memory peak with PETSc memory views:

```bash
+ -memory_view -log_view -log_view_memory
```

- For deeper memory profiling on macOS:

```bash
/usr/bin/time -l <command>
leaks --atExit -- <command>
MallocStackLogging=1 <command>
```

- For Linux/HPC:

```bash
/usr/bin/time -v <command>
valgrind --tool=massif <command>
heaptrack <command>
```

Use PETSc memory logs first because the likely peaks are matrix/preallocation/assembly-related.

### EMI large-mesh scalability

- Colleague’s `potse26` work reduced calls to `insert_points()` for EMI meshes; repeated `MPI_Bcast` in that path can be slow and variable at large communicator sizes.
- Longer-term fix likely requires mesh input/partition/redistribution on a subset of ranks.
- Xavier’s partitioner may change the best strategy; avoid overfitting until that is tested.

### PETSc CUDA scaling

Suggested minimal test matrix:

```text
1 rank / 1 GPU
2 ranks / 2 GPUs
4 ranks / 4 GPUs
```

For each:

```text
no I/O
same solver/preconditioner
without -log_view_gpu_time for timing
with -log_view only for diagnosis
```

Options to inspect:

```text
-ksp_view
-log_view
-mat_type aijcusparse
-vec_type cuda
-use_gpu_aware_mpi
```

Check whether ranks bind one-to-one to GPUs correctly under `srun`.

## Commands Likely Useful In New Session

Fetch/rebase:

```bash
cd src/openCARP
git fetch origin
git status --short
git rebase origin/master
```

Build:

```bash
cd /Users/pezzuto/Desktop/opencarp-work/src/openCARP/build_32bit
ninja -j12

cd /Users/pezzuto/Desktop/opencarp-work/src/openCARP/build_64bit
ninja -j12
```

If local MLIR Python import fails, configure without MLIR or fix the MLIR Python path before building.

Push after rebase:

```bash
cd /Users/pezzuto/Desktop/opencarp-work/src/openCARP
git push --force-with-lease origin issue-461-petsc-64bit-indices
```

## Files Touched Frequently

- `CMakeLists.txt`
- `.gitlab-ci.yml`
- `.gitlab/*.yml`
- `docker/Dockerfile-deps`
- `src/openCARP/cmake/opencarp_types.h.in`
- `numerics/basics.h`
- `numerics/SF_globals.h`
- `numerics/SF_init.h`
- `numerics/petsc/petsc_utils.*`
- `numerics/petsc/SF_petsc_solver.cc`
- `numerics/petsc/SF_petsc_matrix.*`
- `numerics/petsc/SF_petsc_vector.*`
- `numerics/ginkgo/ginkgo_utils.*`
- `fem/slimfem/src/SF_fem_utils_emi.h`
- `fem/slimfem/src/SF_mesh_utils_emi.h`
- `fem/slimfem/src/SF_mesh_io_emi.h`
- `physics/emi.cc`
- `physics/emi.h`
- `physics/limpet/src/*`
- `physics/limpet/src/python/*`
- `physics/limpet/src/make_dynamic_model.sh`
- `simulator/sim_utils.cc`
- `CHANGELOG.md`

## Practical Advice For Next Codex Session

- First inspect `git status --short`; do not blindly stage submodule pointer changes.
- If PETSc preallocation errors return, check whether `max_row_entries_emi*10` for unique/both-face operators was changed.
- If large 64-bit EMI produces NaN volume, suspect mapping/index truncation before assembly, not the volume formula itself.
- If CI MLIR/NVIDIA fails with missing `opencarp_types.h`, inspect generated-header include paths in the custom CUDA/MLIR compile command.
- If command-line PETSc options are “left” but actually applied, inspect Mat/Vec options clearing rather than solver setup.
- Avoid adding debug probes or large temporary containers back into EMI hot paths unless guarded and off by default.
