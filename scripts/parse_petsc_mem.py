#!/usr/bin/env python3
import argparse
import csv
import re
from pathlib import Path


def human_bytes(x: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while abs(x) >= 1024 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    return f"{x:.2f} {units[i]}"


def find_three_numbers(line: str):
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", line, flags=re.I)
    return [float(x) for x in nums]


def parse_petsc_log(path: Path) -> dict:
    out = {}

    for line in path.read_text(errors="ignore").splitlines():
        if line.startswith("Maximum (over computational time) process memory:"):
            nums = find_three_numbers(line)
            out["petsc_proc_mem_total"] = nums[0]
            out["petsc_proc_mem_max"] = nums[1]
            out["petsc_proc_mem_min"] = nums[2]

        elif line.startswith("Maximum (over computational time) space PetscMalloc()ed:"):
            nums = find_three_numbers(line)
            out["petsc_malloc_total"] = nums[0]
            out["petsc_malloc_max"] = nums[1]
            out["petsc_malloc_min"] = nums[2]

        elif line.strip().startswith("Time (sec):"):
            nums = find_three_numbers(line)
            out["time_s"] = nums[0]

        elif line.strip().startswith("Memory (bytes):"):
            nums = find_three_numbers(line)
            out["log_memory_max"] = nums[0]
            out["log_memory_avg"] = nums[2] if len(nums) > 2 else None
            out["log_memory_total"] = nums[3] if len(nums) > 3 else None

    return out

def parse_opencarp_log(path: Path) -> dict:
    out = {}

    for line in path.read_text(errors="ignore").splitlines():
        if "#dofs for emi_mesh:" in line:
            out["emi_dofs"] = int(line.split(":")[-1].strip())

        elif "#elements of emi mesh:" in line:
            out["emi_elements"] = int(line.split(":")[-1].strip())

        elif "#elements of emi surfmesh:" in line:
            out["emi_surf_elements"] = int(line.split(":")[-1].strip())

    return out

def parse_time_log(path: Path) -> dict:
    out = {}

    for line in path.read_text(errors="ignore").splitlines():
        s = line.strip()
        if s.endswith("maximum resident set size"):
            out["rss_bytes"] = float(s.split()[0])
        elif s.endswith("peak memory footprint"):
            out["peak_footprint_bytes"] = float(s.split()[0])

    return out


def append_csv(path: Path, row: dict) -> None:
    fields = [
        "nx", "ny", "nz",
        "emi_dofs",
        "emi_elements",
        "emi_surf_elements",
        "time_s",
        "petsc_proc_mem_total",
        "petsc_proc_mem_max",
        "petsc_malloc_total",
        "petsc_malloc_max",
        "rss_bytes",
        "peak_footprint_bytes",
        "petsc_malloc_bytes_per_dof",
        "petsc_proc_mem_bytes_per_dof",
        "rss_bytes_per_dof",
    ]

    write_header = True

    if path.exists() and path.stat().st_size > 0:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    continue
                # first non-comment, non-empty line → assume header already present
                write_header = False
                break

    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nx", type=int, required=True)
    ap.add_argument("--ny", type=int, required=True)
    ap.add_argument("--nz", type=int, required=True)
    ap.add_argument("--petsc-log", type=Path, required=True)
    ap.add_argument("--time-log", type=Path, required=True)
    ap.add_argument("--csv", type=Path, required=True)
    args = ap.parse_args()

    row = {
        "nx": args.nx,
        "ny": args.ny,
        "nz": args.nz,
    }
    row.update(parse_petsc_log(args.petsc_log))
    row.update(parse_time_log(args.time_log))
    row.update(parse_opencarp_log(args.petsc_log))

    if "emi_dofs" in row:
        row["petsc_malloc_bytes_per_dof"] = row.get("petsc_malloc_total", 0) / row["emi_dofs"]
        row["petsc_proc_mem_bytes_per_dof"] = row.get("petsc_proc_mem_total", 0) / row["emi_dofs"]
        row["rss_bytes_per_dof"] = row.get("rss_bytes", 0) / row["emi_dofs"]

    append_csv(args.csv, row)

    print(f"case: nx={args.nx} ny={args.ny} nz={args.nz}")
    print(f"  time:                 {row.get('time_s', float('nan')):.2f} s")
    print(f"  PETSc process total:  {human_bytes(row.get('petsc_proc_mem_total', 0))}")
    print(f"  PETSc process max:    {human_bytes(row.get('petsc_proc_mem_max', 0))}")
    print(f"  PETSc malloc total:   {human_bytes(row.get('petsc_malloc_total', 0))}")
    print(f"  PETSc malloc max:     {human_bytes(row.get('petsc_malloc_max', 0))}")

    if "rss_bytes" in row:
        print(f"  time -l max RSS:      {human_bytes(row['rss_bytes'])}")
    if "peak_footprint_bytes" in row:
        print(f"  time -l footprint:    {human_bytes(row['peak_footprint_bytes'])}")
    if "emi_dofs" in row:
        print(f"  EMI dofs:              {row['emi_dofs']}")
        print(f"  PETSc malloc / dof:    {human_bytes(row['petsc_malloc_bytes_per_dof'])}/dof")
        print(f"  PETSc proc mem / dof:  {human_bytes(row['petsc_proc_mem_bytes_per_dof'])}/dof")
        if row.get("rss_bytes"):
            print(f"  RSS / dof:            {human_bytes(row['rss_bytes_per_dof'])}/dof")

if __name__ == "__main__":
    main()
