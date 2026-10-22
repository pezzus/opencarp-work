"""Launcher tests: dry runs and mocked srun only; never execute openCARP."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class LauncherTests(unittest.TestCase):
    def launch(self, *args, **changes):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("SLURM_", "EMI_", "OPENCARP_"))}
        env.update(changes)
        return subprocess.run(["bash", str(ROOT / "run.sh"), *args], cwd=ROOT,
                              env=env, text=True, capture_output=True)

    def test_gpu_command(self):
        result = self.launch("--dry-run", "gpu_gmres_gamg.opt", "-tend", "1.25",
                             "+", "-ksp_gmres_restart", "50",
                             SLURM_JOB_NUM_NODES="2", SLURM_NTASKS="8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--ntasks=8", result.stdout)
        self.assertIn("--gpus-per-task=1", result.stdout)
        self.assertIn("-parab_options_file", result.stdout)
        self.assertIn("-mat_type aijcusparse -vec_type cuda", result.stdout)
        self.assertNotIn("-pc_type hypre", result.stdout)
        self.assertIn("-ksp_gmres_restart 50", result.stdout)
        self.assertIn(str(ROOT / "output/bench_job_DRYRUN_gpu_gmres_gamg"), result.stdout)

    def test_cpu_command(self):
        result = self.launch("--dry-run", "cpu_cg_hypre.opt",
                             OPENCARP_CPU_BIN="/path/to/cpu/openCARP",
                             SLURM_JOB_NUM_NODES="2", SLURM_NTASKS="288")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--ntasks=288", result.stdout)
        self.assertIn("-mat_type aij -vec_type standard", result.stdout)
        self.assertIn("env CUDA_VISIBLE_DEVICES=", result.stdout)
        self.assertNotIn("--gpus-per-task", result.stdout)

    def test_cpu_requires_explicit_binary(self):
        result = self.launch("--dry-run", "cpu_cg_hypre.opt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENCARP_CPU_BIN", result.stderr)

    def test_local_real_launch_refused(self):
        result = self.launch("gpu_cg_hypre.opt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Local execution is disabled", result.stderr)

    def test_too_many_gpu_ranks(self):
        result = self.launch("--dry-run", "gpu_cg_hypre.opt", SLURM_NTASKS="144")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at most 4", result.stderr)

    def test_duplicate_separator(self):
        result = self.launch("--dry-run", "gpu_cg_hypre.opt", "+", "+")
        self.assertNotEqual(result.returncode, 0)

    def test_managed_arguments(self):
        result = self.launch("--dry-run", "gpu_cg_hypre.opt", "-simID", "unsafe")
        self.assertNotEqual(result.returncode, 0)

    def test_paired_options_match(self):
        def settings(path):
            return [line for line in path.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        for method in ("cg_hypre", "gmres_hypre", "gmres_gamg"):
            self.assertEqual(settings(ROOT / f"cpu_{method}.opt"),
                             settings(ROOT / f"gpu_{method}.opt"))

    def test_parameter_paths_and_stimulus_tags(self):
        for name, tag in (("rmtest-10x10x10-trim-veri", 561),
                          ("rmtest-6x5x4-trimmed-veri-nosmall", 102)):
            text = (ROOT / f"{name}.par").read_text()
            self.assertIn(f'meshname = "meshes/{name}"', text)
            self.assertIn(f"stim[0].elec.geomID = {tag}", text)
            self.assertRegex(text, r"(?m)^tend = 2$")
            self.assertRegex(text, r"(?m)^dt = 5$")

    def test_alternate_parameter_file(self):
        result = self.launch("--dry-run", "gpu_cg_hypre.opt",
                             EMI_PARAM_FILE="rmtest-6x5x4-trimmed-veri-nosmall.par")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(ROOT / "rmtest-6x5x4-trimmed-veri-nosmall.par"), result.stdout)

    def test_slurm_output_directory_exists(self):
        self.assertIn("#SBATCH --output=output/slurm-%j.out", (ROOT / "run.sh").read_text())
        self.assertTrue((ROOT / "output/.gitkeep").is_file())

    def test_fresh_checkout_without_mesh_data(self):
        # A clone has configs and placeholders, not the multi-GB mesh files.
        # --dry-run should still work without opening any mesh.
        with tempfile.TemporaryDirectory(prefix="emi-layout-test-") as tmp:
            work = Path(tmp) / "exp/emi"
            work.mkdir(parents=True)
            for name in ("gpu_cg_hypre.opt", "rmtest-10x10x10-trim-veri.par"):
                (work / name).write_text((ROOT / name).read_text())
            result = self.launch("--dry-run", "gpu_cg_hypre.opt", EMI_WORKDIR=str(work))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(work / "output/bench_job_DRYRUN_gpu_cg_hypre"), result.stdout)
            self.assertFalse((work / "output").exists(), "Dry runs must not create outputs")

    def test_mock_launch_preserves_status_and_snapshots(self):
        with tempfile.TemporaryDirectory(prefix="emi-launch-test-") as tmp:
            tmp = Path(tmp)
            # This fake launcher records arguments and exits; it never starts
            # the supplied application, MPI, or the simulation.
            fake = tmp / "srun"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$EMI_TEST_ARGS"\n'
                            'printf "%s\\n" "$CUDA_VISIBLE_DEVICES" "$OMP_NUM_THREADS" '
                            '> "$EMI_TEST_ENV"\nexit 7\n')
            fake.chmod(0o755)
            run = tmp / "result"
            result = self.launch(
                "cpu_gmres_gamg.opt", "+", "-ksp_rtol", "1e-50",
                OPENCARP_CPU_BIN="/bin/true", SLURM_JOB_ID="MOCK",
                SLURM_NTASKS="144", SLURM_JOB_NUM_NODES="1",
                EMI_RUN_DIR=str(run), EMI_TEST_ARGS=str(tmp / "args"),
                EMI_TEST_ENV=str(tmp / "environment"),
                PATH=f"{tmp}:{os.environ['PATH']}")
            self.assertEqual(result.returncode, 7, result.stderr)
            meta = Path(str(run) + ".metadata")
            for name in ("input.par", "solver.opt", "run.sh", "command.txt",
                         "environment.txt", "status.txt"):
                self.assertTrue((meta / name).is_file(), name)
            self.assertIn("exit code: 7", (meta / "status.txt").read_text())
            self.assertEqual((tmp / "environment").read_text(), "\n1\n")
            self.assertIn("-parab_options_file", (tmp / "args").read_text())
            self.assertFalse(run.exists(), "The application must not be executed")
            # The saved metadata alone is enough to prevent overwriting a run.
            duplicate = self.launch("--dry-run", "gpu_cg_hypre.opt", EMI_RUN_DIR=str(run))
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("already exists", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()
