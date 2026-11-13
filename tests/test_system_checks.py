import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestSystemChecksScript(unittest.TestCase):
    """Verify bash/system_checks.sh behavior across normal and failure modes."""

    @classmethod
    def setUpClass(cls) -> None:
        """Locate the repository root and target Bash script once per test run."""
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.script_path = cls.project_root / "bash" / "system_checks.sh"

    def setUp(self) -> None:
        """Create an isolated temporary environment for each individual test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Build a valid mock /proc structure
        self.mock_proc = self.temp_path / "proc"
        (self.mock_proc / "net").mkdir(parents=True, exist_ok=True)
        for rel_file in ("stat", "meminfo", "uptime", "loadavg", "net/dev"):
            file_path = self.mock_proc / rel_file
            file_path.write_text("mock kernel data\n", encoding="utf-8")

        # Build a valid mock project root with runtime directories
        self.mock_root = self.temp_path / "project"
        for runtime_dir in ("logs", "data", "config"):
            (self.mock_root / runtime_dir).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        """Restore permissions if modified during a test, then clean up."""
        for root, dirs, files in os.walk(self.temp_path):
            for name in dirs:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
        self.temp_dir.cleanup()

    def _run_script(self, args=None, env_overrides=None) -> subprocess.CompletedProcess:
        """Execute system_checks.sh with optional CLI arguments and env variables."""
        cmd = [str(self.script_path)]
        if args:
            cmd.extend(args)

        env = os.environ.copy()
        env["LHM_PROC_DIR"] = str(self.mock_proc)
        env["LHM_PROJECT_ROOT"] = str(self.mock_root)
        if env_overrides:
            env.update(env_overrides)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_script_exists_and_is_executable(self) -> None:
        """Ensure bash/system_checks.sh exists and has the user execute bit set."""
        self.assertTrue(
            self.script_path.is_file(),
            f"Script not found at {self.script_path}",
        )
        self.assertTrue(
            os.access(self.script_path, os.X_OK),
            f"Script {self.script_path} is not executable (run chmod +x)",
        )

    def test_passing_environment_returns_zero(self) -> None:
        """Verify exit code 0 when all /proc files, commands, and dirs are valid."""
        result = self._run_script()
        self.assertEqual(result.returncode, 0, f"Unexpected stderr: {result.stderr}")
        self.assertIn("System environment verification passed.", result.stdout)

    def test_quiet_flag_suppresses_stdout(self) -> None:
        """Verify --quiet suppresses informational output while returning 0."""
        result = self._run_script(args=["--quiet"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_invalid_argument_returns_exit_code_1(self) -> None:
        """Verify unknown CLI flags are rejected with exit code 1 and stderr message."""
        result = self._run_script(args=["--invalid-flag"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("[ERROR] Unknown argument: --invalid-flag", result.stderr)

    def test_missing_proc_file_returns_exit_code_2(self) -> None:
        """Verify a missing /proc file triggers exit code 2."""
        (self.mock_proc / "meminfo").unlink()
        result = self._run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Required kernel interface file is missing", result.stderr)

    def test_unreadable_proc_file_returns_exit_code_2(self) -> None:
        """Verify a permission-denied /proc file triggers exit code 2."""
        if os.geteuid() == 0:
            self.skipTest("Root user bypasses read permission checks.")
        target_file = self.mock_proc / "stat"
        target_file.chmod(0o000)
        result = self._run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not readable", result.stderr)

    def test_missing_command_returns_exit_code_3(self) -> None:
        """Verify a missing required binary triggers exit code 3."""
        result = self._run_script(
            env_overrides={"LHM_REQUIRED_CMDS": "df non_existent_binary_xyz_123"}
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("non_existent_binary_xyz_123", result.stderr)

    def test_missing_runtime_directory_returns_exit_code_4(self) -> None:
        """Verify a missing runtime directory (e.g., logs/) triggers exit code 4."""
        (self.mock_root / "logs").rmdir()
        result = self._run_script()
        self.assertEqual(result.returncode, 4)
        self.assertIn("Required directory is missing", result.stderr)

    def test_non_writable_runtime_directory_returns_exit_code_4(self) -> None:
        """Verify a read-only runtime directory triggers exit code 4."""
        if os.geteuid() == 0:
            self.skipTest("Root user bypasses write permission checks.")
        logs_dir = self.mock_root / "logs"
        logs_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        result = self._run_script()
        self.assertEqual(result.returncode, 4)
        self.assertIn("Required directory is not writable", result.stderr)


if __name__ == "__main__":
    unittest.main()