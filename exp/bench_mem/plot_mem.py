#!/usr/bin/env python3
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

csv_path = Path("bench_logs/memory_results.csv")

title = "Memory scaling"
with csv_path.open() as f:
    first_line = f.readline().strip()
    if first_line.startswith("#"):
        title = first_line[1:].strip()

df = pd.read_csv(csv_path, comment="#")
df = df.sort_values("emi_dofs")

# --- Memory per DOF ---
plt.figure(figsize=(7, 5))

plt.plot(
    df["emi_dofs"],
    df["petsc_malloc_bytes_per_dof"] / 1024,
    "o-",
    label="PETSc malloc",
)

plt.plot(
    df["emi_dofs"],
    df["petsc_proc_mem_bytes_per_dof"] / 1024,
    "o-",
    label="PETSc process memory",
)

if "rss_bytes_per_dof" in df.columns:
    plt.plot(
        df["emi_dofs"],
        df["rss_bytes_per_dof"] / 1024,
        "o-",
        label="time -l RSS",
    )

plt.xscale("log")
plt.xlabel("EMI DOFs")
plt.ylabel("Memory per DOF [KB/DOF]")
plt.title(title)
plt.grid(True, which="both")
plt.legend()
plt.tight_layout()
plt.savefig("bench_logs/memory_per_dof.png", dpi=200)


# --- Timing scaling ---
plt.figure(figsize=(7, 5))

plt.plot(
    df["emi_dofs"],
    df["time_s"],
    "o-",
    label="Total time",
)

plt.xscale("log")
plt.yscale("log")
plt.xlabel("EMI DOFs")
plt.ylabel("Time [s]")
plt.title(title)
plt.grid(True, which="both")
plt.legend()
plt.tight_layout()
plt.savefig("bench_logs/timing_scaling.png", dpi=200)


# --- Absolute memory usage ---
plt.figure(figsize=(7, 5))

plt.plot(
    df["emi_dofs"],
    df["petsc_malloc_total"] / (1024**3),
    "o-",
    label="PETSc malloc",
)

plt.plot(
    df["emi_dofs"],
    df["petsc_proc_mem_total"] / (1024**3),
    "o-",
    label="PETSc process memory",
)

if "rss_bytes" in df.columns:
    plt.plot(
        df["emi_dofs"],
        df["rss_bytes"] / (1024**3),
        "o-",
        label="time -l RSS",
    )

plt.xscale("log")
plt.yscale("log")
plt.xlabel("EMI DOFs")
plt.ylabel("Memory [GB]")
plt.title(title)
plt.grid(True, which="both")
plt.legend()
plt.tight_layout()
plt.savefig("bench_logs/memory_absolute.png", dpi=200)

plt.show()
