# openCARP work environment

Portable workspace for openCARP development, PETSc 32/64-bit builds, EMI memory
benchmarks, and postprocessing utilities.

The outer repository tracks the working environment. The openCARP source tree is
currently checked out in `src/openCARP` and is not yet an outer-repo submodule.

## Layout

| Path | Purpose |
| --- | --- |
| `src/openCARP` | openCARP development checkout |
| `conf/arch` | machine-specific environment profiles |
| `scripts` | setup, build, conversion, and benchmark helpers |
| `deps` | locally built dependencies, ignored by git |
| `exp` | experiment inputs and benchmark workspaces |
| `notes` | handoff notes and machine-specific planning |

## CSCS Install

Compilation on a node:

```bash
srun -A lp157 --uenv=prgenv-gnu/26.3:v1 --view=default --partition debug --pty bash
```

Install `uv` in your `.local/bin` (so you can see in the path), then do:
```bash
uv sync
```
in the root folder.

```bash
mkdir deps && cd deps
LLVM_BUILD_JOBS=128 ../scripts/config-llvm.sh
../scripts/config-petsc.sh 3.26.0 double 32 opt
```

```bash
cd ../src
git clone git@git.opencarp.org:openCARP/openCARP.git
cd openCARP
git switch cscs-gpu
../../scripts/config-opencarp.sh 32
```

## Install

macOS is the only fully configured profile at the moment.

```bash
cd ~/Desktop/opencarp-work
uv sync
source conf/arch/macos.sh
```

Build PETSc from `deps`:

```bash
cd deps
../scripts/config-petsc.sh 3.26.0 double 32 opt
../scripts/config-petsc.sh 3.26.0 double 64 opt
```

Build LLVM/MLIR if MLIR code generation is needed:

```bash
cd deps
../scripts/config-llvm.sh
```

Configure and build openCARP:

```bash
cd ~/Desktop/opencarp-work
scripts/config-opencarp.sh 32
scripts/config-opencarp.sh 64
```

## Usage

Load the local profile before running tools:

```bash
cd ~/Desktop/opencarp-work
source conf/arch/macos.sh
```

The profile defines:

```bash
OC_WORK   # workspace root
OC32_BIN  # 32-bit openCARP binary
OC64_BIN  # 64-bit openCARP binary
PETSC_DIR # local PETSc source/build tree
```

Run the EMI memory benchmark:

```bash
cd exp/bench_mem
../../scripts/bench_mem.sh
```

Optional controls:

```bash
NP=8 ../../scripts/bench_mem.sh
OC64_BIN=/path/to/openCARP ../../scripts/bench_mem.sh "-ksp_type cg -pc_type gamg"
```

Results are written to:

```text
exp/bench_mem/bench_logs/memory_results.csv
```

## Machine Profiles

| Machine | Status | Profile |
| --- | --- | --- |
| macOS | configured | `conf/arch/macos.sh` |
| CSCS Alps Daint | planned | `conf/arch/daint.sh` |
| Windows + WSL2 workstation | planned | `conf/arch/wsl2.sh` |

See `notes/MACHINE_PROFILES.md` for the expansion plan and
`notes/OPENCARP_64BIT_HANDOFF.md` for the current openCARP 64-bit branch
handoff.
