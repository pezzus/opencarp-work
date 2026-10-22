#!/usr/bin/env python3
"""One-rank CUDA output/activation ablation; preserve logs and effective inputs."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import time


def variant_config(source, fields, activation):
    if not activation:
        source = re.sub(r"^lats\[.*\n?", "", source, flags=re.M)
        source = re.sub(r"^num_LATs\s*=.*$", "num_LATs = 0", source, flags=re.M)
        source += "\ncompute_APD = 0\nt_sentinel = 0\n"
    if not fields:
        # Disable field serialization while preserving the output-step cadence.
        # This avoids conflating less frequent output_step() calls with disk I/O.
        source = re.sub(r"^(gridout_[ie]|dataout_[ie])\s*=.*$",
                        r"\1 = 0", source, flags=re.M)
        source += ("\ngridout_p = 0\ngridout_emi = 0\ndump_data = 0\n"
                   "num_gvecs = 0\nnum_trace = 0\nnum_tsav = 0\n"
                   "chkpt_intv = 0\ndump2MatLab = 0\ndump_protocol = 0\n"
                   "output_setup = 0\n")
    return source


def physics_times(log):
    result = {}
    for name, prefix in (("Electrics", "electric"), ("Myocard Ionics", "ionic")):
        match = re.search(re.escape(name) +
                          r":\s*Init:\s*([\d.]+) sec\s*Compute:\s*([\d.]+) sec"
                          r"\s*Output:\s*([\d.]+) sec", log)
        if match:
            result.update({prefix + "_" + phase: float(value)
                           for phase, value in zip(("init", "compute", "output"), match.groups())})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--duration-ms", type=float, default=10.0)
    parser.add_argument("--host-ionic-mapping", action="store_true",
                        help="A/B control: gather/scatter noncontiguous ionic data on the CPU")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--variants", nargs="+",
                        choices=["baseline", "no_fields", "no_activation", "neither"],
                        default=["baseline", "no_fields", "no_activation", "neither"])
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.duration_ms < 4:
        parser.error("--duration-ms must cover the 4 ms stimulus")
    root = Path(__file__).resolve().parent
    binary = root.parent.parent / "src/openCARP/build_32bit/bin/openCARP"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = (root / "benchmark_450um.par").read_text()
    source = re.sub(r"^tend\s*=.*$", f"tend = {args.duration_ms:g}", source, flags=re.M)
    source = re.sub(r"^stim\[0\]\.ptcl\.bcl\s*=.*$",
                    f"stim[0].ptcl.bcl = {args.duration_ms:g}", source, flags=re.M)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="0")
    env.pop("CUDA_LAUNCH_BLOCKING", None)
    env["OPENCARP_CUDA_INDEXED_DATA"] = "0" if args.host_ionic_mapping else "1"
    metadata = dict(binary=str(binary), cpu=args.cpu,
                    environment={key: env[key] for key in (
                        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                        "MKL_NUM_THREADS", "CUDA_VISIBLE_DEVICES", "OPENCARP_CUDA_INDEXED_DATA")},
                    repeats=args.repeats, variants=args.variants, duration_ms=args.duration_ms,
                    timing_note="Physics timers are local elapsed timers; phase timestamps "
                    "are stdout-observed approximations, not GPU-synchronized timers.")
    repo = root.parent.parent / "src/openCARP"
    metadata["source_commit"] = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    source_diff = subprocess.check_output(["git", "-C", str(repo), "diff"], text=True)
    (output / "source.diff").write_text(source_diff)
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows = []
    for repeat in range(1, args.repeats + 1):
        # Rotate order so each case is not always in the same warm-up position.
        shift = (repeat - 1) % len(args.variants)
        order = args.variants[shift:] + args.variants[:shift]
        for variant in order:
            run = output / f"{variant}_{repeat}"
            config = output / f"{variant}_{repeat}.par"
            config.write_text(variant_config(source, variant not in ("no_fields", "neither"),
                                             variant not in ("no_activation", "neither")))
            command = ["taskset", "-c", str(args.cpu), "/usr/bin/time", "-v",
                       "-o", str(output / f"{run.name}.time"),
                       str(binary), "+F", str(config), "-simID", str(run),
                       "-num_io_nodes", "0", "+", "-mat_type", "aijcusparse",
                       "-vec_type", "cuda", "-ksp_type", "cg", "-pc_type", "jacobi"]
            start = time.monotonic()
            launched = finished_loop = None
            print(f"Starting {run.name}", flush=True)
            with (output / f"{run.name}.log").open("w") as logfile:
                proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in proc.stdout:
                    logfile.write(line)
                    if "*** Launching simulation ***" in line:
                        launched = time.monotonic()
                    if "Timings of individual physics:" in line:
                        finished_loop = time.monotonic()
                rc = proc.wait()
            end = time.monotonic()
            log = (output / f"{run.name}.log").read_text()
            row = dict(variant=variant, repeat=repeat, returncode=rc,
                       wall=end-start, **physics_times(log))
            detectors = re.search(r"Activation detection: (\d+) detectors, APD=(\d+), sentinel=(\d+)", log)
            evaluations = re.search(r"Activation detection: (\d+) calls, (\d+) detector evaluations", log)
            if detectors:
                row["activation_detectors"] = int(detectors[1])
            if evaluations:
                row["activation_calls"] = int(evaluations[1])
                row["activation_evaluations"] = int(evaluations[2])
            row["cuda_mapping_regions"] = len(re.findall(r"CUDA ionic mapping: region=", log))
            if launched is not None and finished_loop is not None:
                row.update(startup_observed=launched-start,
                           loop_observed=finished_loop-launched,
                           finalization_observed=end-finished_loop)
            timer_log = (output / f"{run.name}.time").read_text()
            rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", timer_log)
            if rss:
                row["max_rss_kib"] = int(rss[1])
            row["output_bytes"] = sum(p.stat().st_size for p in run.rglob("*") if p.is_file())
            rows.append(row)
            (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
            keys = list(dict.fromkeys(key for entry in rows for key in entry))
            with (output / "results.csv").open("w", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=keys)
                writer.writeheader()
                writer.writerows(rows)
            print(json.dumps(row), flush=True)
            if rc:
                raise SystemExit(f"{run.name} failed; see its log")
            if variant in ("no_activation", "neither") and (
                    not detectors or not evaluations or int(detectors[1]) != 0
                    or int(detectors[2]) != 0 or int(detectors[3]) != 0
                    or int(evaluations[2]) != 0):
                raise SystemExit(f"{run.name}: activation-off verification failed")


if __name__ == "__main__":
    main()
