# Machine Profiles

Planning notes for keeping the openCARP work environment usable across the Mac,
CSCS Alps Daint, and the Windows + WSL2 workstation.

The README should stay focused on install and usage. Keep longer setup notes,
machine assumptions, and open questions here.

## Shared Conventions

- Keep machine-specific environment in `conf/arch/*.sh`.
- Keep generated dependencies and build trees out of git.
- Keep source code in `src/openCARP`; it may become a submodule later.
- Keep experiments under `exp/`, with large outputs ignored unless curated.
- Prefer scripts that accept parameters over scripts that bake in one machine.

## macOS

Status: configured.

Current profile:

```bash
source conf/arch/macos.sh
```

Current local assumptions:

- Workspace root: `~/Desktop/opencarp-work`
- openCARP checkout: `src/openCARP`
- PETSc: `deps/petsc-3.25.1`
- LLVM/MLIR: `deps/llvm-project-18.1.8.src`
- Gmsh app: `/Applications/Gmsh.app/Contents/MacOS/gmsh`
- Python environment managed by `uv`

Useful scripts:

- `scripts/config-petsc.sh`
- `scripts/config-llvm.sh`
- `scripts/config-opencarp.sh`
- `scripts/bench_mem.sh`

Known local caveat:

- The last local 64-bit rebuild in the handoff failed before compiling touched
  code because Python could not import `mlir.ir`. Either fix the local MLIR
  Python path or configure openCARP without MLIR code generation.

## CSCS Alps Daint

Status: planned.

Access model:

- SSH access goes through the CSCS Ela jump host.
- Daint login target: `daint.alps.cscs.ch`.
- SSH uses CSCS-signed keys, not password authentication.
- Jobs run through Slurm.

Relevant CSCS docs:

- SSH: https://docs.cscs.ch/access/ssh/
- Daint: https://docs.cscs.ch/clusters/daint/
- Slurm: https://docs.cscs.ch/running/slurm/
- uenv: https://docs.cscs.ch/software/uenv/
- prgenv-gnu: https://docs.cscs.ch/software/prgenv/prgenv-gnu/

Expected direction:

- Add `conf/arch/daint.sh`.
- Use CSCS `uenv` environments rather than relying on the legacy Cray module
  workflow.
- Start with `prgenv-gnu` unless openCARP needs a more specialized stack.
- Add Slurm scripts under a future `scripts/slurm/` or `conf/slurm/` location.
- Decide whether PETSc/LLVM/openCARP are built in scratch, project storage, or
  a per-user software prefix.

Open questions:

- Which CSCS project/account should Slurm jobs charge?
- Which uenv version should be pinned for reproducibility?
- Should Daint builds use local dependency builds, Spack/uenv-provided
  dependencies, or containers?
- What is the desired split between CPU-only, GPU-enabled PETSc, and Ginkgo
  experiments?
- Which filesystem should hold large EMI meshes and benchmark outputs?

## Windows + WSL2 Workstation

Status: planned.

Access model:

- Windows host is reachable over Tailscale.
- Remote Desktop is available for GUI/admin access.
- openCARP build and CLI work should happen inside WSL2.

Expected direction:

- Add `conf/arch/wsl2.sh`.
- Keep Linux build paths inside the WSL filesystem for performance.
- Use Windows paths only for deliberate file exchange, visualization, or backup.
- Reuse the same outer workspace layout inside WSL2.
- Keep Remote Desktop/Tailscale details out of scripts unless automation needs
  them.

Open questions:

- Which WSL2 distribution is the target?
- Which MPI/compiler stack should be used in WSL2?
- Should PETSc and LLVM be built from source as on macOS, or installed through
  system packages where possible?
- Where should large experiment outputs be stored: WSL disk, Windows disk, or
  external storage?
