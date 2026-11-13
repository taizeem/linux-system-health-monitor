import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestCollectMetricsScript(unittest.TestCase):
    """Verify bash/collect_metrics.sh output contract and error handling."""

    @classmethod
    def setUpClass(cls) -> None:
        """Locate the repository root and collector script."""
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.script_path = cls.project_root / "bash" / "collect_metrics.sh"

    def setUp(self) -> None:
        """Create an isolated mock /proc and project root for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.mock_proc = self.temp_path / "proc"
        self.mock_proc.mkdir(parents=True, exist_ok=True)

        self.mock_stat = self.mock_proc / "stat"
        self.mock_stat.write_text(
            "cpu  1000 20 300 8000 50 10 5 0 0 0\n"
            "cpu0 500 10 150 4000 25 5 2 0 0 0\n",
            encoding="utf-8",
        )

        self.mock_meminfo = self.mock_proc / "meminfo"
        self.mock_meminfo.write_text(
            "MemTotal:       16384000 kB\n"
            "MemFree:         4096000 kB\n"
            "MemAvailable:   10240000 kB\n"
            "Buffers:          512000 kB\n"
            "Cached:          5632000 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1572864 kB\n",
            encoding="utf-8",
        )

        self.mock_root = self.temp_path / "project"
        for runtime_dir in ("logs", "data", "config"):
            (self.mock_root / runtime_dir).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        """Restore permissions and remove temporary test directory."""
        for root, dirs, files in os.walk(self.temp_path):
            for name in dirs:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
        self.temp_dir.cleanup()

    def _run_collector(self, args=None, env_overrides=None) -> subprocess.CompletedProcess:
        """Run collect_metrics.sh with fast 10ms sample interval."""
        cmd = [str(self.script_path)]
        if args:
            cmd.extend(args)

        env = os.environ.copy()
        env["LHM_PROC_DIR"] = str(self.mock_proc)
        env["LHM_PROJECT_ROOT"] = str(self.mock_root)
        env["LHM_LOG_FILE"] = str(self.mock_root / "logs" / "monitor.log")
        env["LHM_CPU_SAMPLE_INTERVAL"] = "0.01"
        if env_overrides:
            env.update(env_overrides)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    @staticmethod
    def _parse_key_values(stdout: str) -> dict:
        """Parse KEY=VALUE lines from stdout into a Python dictionary."""
        parsed = {}
        for line in stdout.strip().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key] = value
        return parsed

    def test_script_is_executable(self) -> None:
        """Verify bash/collect_metrics.sh exists and has execute permissions."""
        self.assertTrue(self.script_path.is_file())
        self.assertTrue(os.access(self.script_path, os.X_OK))

    def test_valid_collection_emits_expected_keys(self) -> None:
        """Verify all CPU1, CPU2, MEM, and SWAP keys are emitted accurately."""
        result = self._run_collector()
        self.assertEqual(result.returncode, 0, f"Stderr: {result.stderr}")
        data = self._parse_key_values(result.stdout)

        self.assertIn("TIMESTAMP_UTC", data)
        self.assertEqual(data["CPU1_USER"], "1000")
        self.assertEqual(data["CPU1_IDLE"], "8000")
        self.assertEqual(data["CPU2_SYSTEM"], "300")
        self.assertEqual(data["MEM_TOTAL_KB"], "16384000")
        self.assertEqual(data["MEM_AVAILABLE_KB"], "10240000")
        self.assertEqual(data["SWAP_TOTAL_KB"], "2097152")
        self.assertEqual(data["SWAP_FREE_KB"], "1572864")

    def test_mem_available_fallback_when_omitted(self) -> None:
        """Verify MemAvailable falls back to MemFree + Buffers + Cached if missing."""
        self.mock_meminfo.write_text(
            "MemTotal:       8000000 kB\n"
            "MemFree:        1000000 kB\n"
            "Buffers:         500000 kB\n"
            "Cached:         1500000 kB\n"
            "SwapTotal:            0 kB\n"
            "SwapFree:             0 kB\n",
            encoding="utf-8",
        )
        result = self._run_collector()
        self.assertEqual(result.returncode, 0)
        data = self._parse_key_values(result.stdout)
        self.assertEqual(data["MEM_AVAILABLE_KB"], "3000000")

    def test_missing_stat_file_returns_exit_code_2(self) -> None:
        """Verify missing /proc/stat exits with code 2."""
        self.mock_stat.unlink()
        result = self._run_collector()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Cannot read CPU stat file", result.stderr)

    def test_corrupted_stat_file_returns_exit_code_5(self) -> None:
        """Verify non-numeric CPU counters in /proc/stat trigger exit code 5."""
        self.mock_stat.write_text("cpu  1000 CORRUPT 300 8000 50 10 5 0\n", encoding="utf-8")
        result = self._run_collector()
        self.assertEqual(result.returncode, 5)
        self.assertIn("Malformed non-integer CPU counter", result.stderr)

    def test_missing_cpu_line_returns_exit_code_5(self) -> None:
        """Verify /proc/stat without aggregate 'cpu ' line triggers exit code 5."""
        self.mock_stat.write_text("intr 12345\n", encoding="utf-8")
        result = self._run_collector()
        self.assertEqual(result.returncode, 5)
        self.assertIn("Aggregate 'cpu ' line not found", result.stderr)

    def test_corrupted_meminfo_returns_exit_code_5(self) -> None:
        """Verify missing MemTotal in /proc/meminfo triggers exit code 5."""
        self.mock_meminfo.write_text("MemFree: 1024 kB\n", encoding="utf-8")
        result = self._run_collector()
        self.assertEqual(result.returncode, 5)
        self.assertIn("Malformed or missing memory counter", result.stderr)

    def test_invalid_cli_argument_returns_exit_code_1(self) -> None:
        """Verify unknown CLI flags return exit code 1."""
        result = self._run_collector(args=["--unknown"])
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()