import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


class TestCronScripts(unittest.TestCase):
    """Verify non-blocking flock concurrency and idempotent crontab management."""

    @classmethod
    def setUpClass(cls) -> None:
        """Locate project root and cron scripts."""
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.runner_script = cls.project_root / "bash" / "cron_runner.sh"
        cls.setup_script = cls.project_root / "bash" / "setup_cron.sh"

    def setUp(self) -> None:
        """Create temporary state directories and mock crontab binary."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.mock_root = self.temp_path / "project"
        for d in ("logs", "data", "config"):
            (self.mock_root / d).mkdir(parents=True, exist_ok=True)

        self.lock_file = self.mock_root / "data" / "cron_monitor.lock"
        self.cron_log = self.mock_root / "logs" / "cron.log"
        self.monitor_log = self.mock_root / "logs" / "monitor.log"

        # Build a mock crontab binary that reads/writes a local file in temp_path
        self.crontab_store = self.temp_path / "mock_user_crontab"
        self.mock_crontab_bin = self.temp_path / "mock_crontab"
        self.mock_crontab_bin.write_text(
            f'#!/usr/bin/env bash\n'
            f'STORE="{self.crontab_store}"\n'
            f'if [[ "$1" == "-l" ]]; then\n'
            f'    [[ -f "$STORE" ]] && cat "$STORE" || exit 1\n'
            f'elif [[ "$1" == "-" ]]; then\n'
            f'    cat > "$STORE"\n'
            f'fi\n',
            encoding="utf-8",
        )
        self.mock_crontab_bin.chmod(stat.S_IRWXU)

    def tearDown(self) -> None:
        """Clean up temporary test directory."""
        self.temp_dir.cleanup()

    def test_scripts_exist_and_are_executable(self) -> None:
        """Verify cron_runner.sh and setup_cron.sh exist and are executable."""
        self.assertTrue(self.runner_script.is_file())
        self.assertTrue(os.access(self.runner_script, os.X_OK))
        self.assertTrue(self.setup_script.is_file())
        self.assertTrue(os.access(self.setup_script, os.X_OK))

    def test_cron_runner_succeeds_with_mock_main(self) -> None:
        """Verify cron_runner.sh acquires lock and executes main script cleanly."""
        mock_main = self.temp_path / "mock_main.py"
        mock_main.write_text("import sys; sys.exit(0)\n", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "LHM_PROJECT_ROOT": str(self.mock_root),
                "LHM_LOCK_FILE": str(self.lock_file),
                "LHM_CRON_LOG_FILE": str(self.cron_log),
                "LHM_LOG_FILE": str(self.monitor_log),
                "LHM_MAIN_SCRIPT": str(mock_main),
            }
        )
        res = subprocess.run(
            [str(self.runner_script)], capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual(res.returncode, 0, f"Stderr: {res.stderr}")
        self.assertTrue(self.lock_file.is_file())

    def test_cron_runner_exits_with_code_6_when_lock_is_already_held(self) -> None:
        """Verify overlapping cron_runner.sh execution is rejected with exit code 6."""
        mock_main = self.temp_path / "mock_main.py"
        mock_main.write_text("import sys; sys.exit(0)\n", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "LHM_PROJECT_ROOT": str(self.mock_root),
                "LHM_LOCK_FILE": str(self.lock_file),
                "LHM_CRON_LOG_FILE": str(self.cron_log),
                "LHM_LOG_FILE": str(self.monitor_log),
                "LHM_MAIN_SCRIPT": str(mock_main),
            }
        )

        # Hold an exclusive flock on self.lock_file in a background process
        holder = subprocess.Popen(
            ["flock", "-x", str(self.lock_file), "sleep", "5"]
        )
        try:
            # Wait briefly until lockfile exists
            while not self.lock_file.exists():
                pass
            res = subprocess.run(
                [str(self.runner_script)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(res.returncode, 6)
            self.assertIn("Skipping cron run", res.stderr)
        finally:
            holder.terminate()
            holder.wait()

    def test_setup_cron_print_and_invalid_schedule_validation(self) -> None:
        """Verify --print outputs valid crontab entry and rejects bad schedules."""
        res_ok = subprocess.run(
            [str(self.setup_script), "--print", "*/10 * * * *"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_ok.returncode, 0)
        self.assertIn("*/10 * * * *", res_ok.stdout)
        self.assertIn("# LHM_MANAGED_CRON_JOB", res_ok.stdout)

        res_bad = subprocess.run(
            [str(self.setup_script), "--print", "* * *"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_bad.returncode, 1)
        self.assertIn("expected 5 fields", res_bad.stderr)

    def test_setup_cron_install_idempotency_status_and_remove(self) -> None:
        """Verify --install is idempotent (no duplicate entries) and --remove cleans up."""
        env = os.environ.copy()
        env["LHM_CRONTAB_BIN"] = str(self.mock_crontab_bin)
        env["LHM_LOG_FILE"] = str(self.monitor_log)

        # Seed existing unrelated cron job
        self.crontab_store.write_text("0 0 * * * /usr/bin/backup.sh\n", encoding="utf-8")

        # Install twice with different schedules to verify update without duplication
        subprocess.run(
            [str(self.setup_script), "--install", "*/5 * * * *"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            [str(self.setup_script), "--install", "*/15 * * * *"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

        content = self.crontab_store.read_text(encoding="utf-8")
        self.assertIn("0 0 * * * /usr/bin/backup.sh", content)
        self.assertIn("*/15 * * * *", content)
        self.assertNotIn("*/5 * * * *", content)
        self.assertEqual(content.count("# LHM_MANAGED_CRON_JOB"), 1)

        # Verify --remove deletes only the managed job and keeps backup.sh
        subprocess.run(
            [str(self.setup_script), "--remove"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        after_remove = self.crontab_store.read_text(encoding="utf-8")
        self.assertIn("0 0 * * * /usr/bin/backup.sh", after_remove)
        self.assertNotIn("# LHM_MANAGED_CRON_JOB", after_remove)


if __name__ == "__main__":
    unittest.main()
