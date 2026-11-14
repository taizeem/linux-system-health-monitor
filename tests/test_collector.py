from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

# Ensure the project root is importable so 'python.collector' resolves cleanly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.collector import (  # noqa: E402
    CollectorError,
    RawSystemMetrics,
    collect_raw_metrics,
    parse_raw_metrics,
    run_system_checks,
)


VALID_SAMPLE_STDOUT = """\
TIMESTAMP_UTC=2026-11-14T03:45:00Z
CPU1_USER=1000
CPU1_NICE=20
CPU1_SYSTEM=300
CPU1_IDLE=8000
CPU1_IOWAIT=50
CPU1_IRQ=10
CPU1_SOFTIRQ=5
CPU1_STEAL=0
CPU2_USER=1025
CPU2_NICE=20
CPU2_SYSTEM=310
CPU2_IDLE=8060
CPU2_IOWAIT=50
CPU2_IRQ=10
CPU2_SOFTIRQ=5
CPU2_STEAL=0
MEM_TOTAL_KB=16384000
MEM_FREE_KB=4096000
MEM_AVAILABLE_KB=10240000
MEM_BUFFERS_KB=512000
MEM_CACHED_KB=5632000
SWAP_TOTAL_KB=2097152
SWAP_FREE_KB=1572864
DISK_MOUNT=/
DISK_TOTAL_KB=104857600
DISK_USED_KB=41943040
DISK_AVAILABLE_KB=62914560
RUNNING_PROCESSES=142
NET_RX_BYTES=500000
NET_TX_BYTES=250000
NETWORK_STATUS=Connected
"""


class TestCollectorModule(unittest.TestCase):
    """Test suite covering parsing, validation, and subprocess edge cases."""

    def setUp(self) -> None:
        """Create a temporary directory for mock scripts and files."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        """Restore permissions and clean up temporary directory."""
        for root, dirs, files in os.walk(self.temp_path):
            for name in dirs:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
        self.temp_dir.cleanup()

    def test_parse_valid_stdout_returns_raw_system_metrics(self) -> None:
        """Verify valid KEY=VALUE text parses into an immutable RawSystemMetrics object."""
        metrics = parse_raw_metrics(VALID_SAMPLE_STDOUT)
        self.assertIsInstance(metrics, RawSystemMetrics)
        self.assertEqual(metrics.timestamp_utc, "2026-11-14T03:45:00Z")
        self.assertEqual(metrics.cpu_sample_1.user, 1000)
        self.assertEqual(metrics.cpu_sample_1.idle_all, 8050)
        self.assertEqual(metrics.cpu_sample_1.total, 9385)
        self.assertEqual(metrics.cpu_sample_2.total, 9480)
        self.assertEqual(metrics.mem_total_kb, 16384000)
        self.assertEqual(metrics.mem_available_kb, 10240000)
        self.assertEqual(metrics.disk_mount, "/")
        self.assertEqual(metrics.running_processes, 142)
        self.assertEqual(metrics.network_status, "Connected")

    def test_raw_system_metrics_is_immutable(self) -> None:
        """Verify RawSystemMetrics fields cannot be mutated after creation."""
        metrics = parse_raw_metrics(VALID_SAMPLE_STDOUT)
        with self.assertRaises(FrozenInstanceError):
            metrics.running_processes = 999  # type: ignore[misc]

    def test_empty_stdout_raises_collector_error(self) -> None:
        """Verify empty stdout raises CollectorError."""
        with self.assertRaisesRegex(CollectorError, "empty stdout"):
            parse_raw_metrics("   \n  ")

    def test_malformed_line_without_equals_raises_collector_error(self) -> None:
        """Verify a line violating KEY=VALUE syntax raises CollectorError."""
        corrupted = VALID_SAMPLE_STDOUT + "\nTHIS_LINE_HAS_NO_EQUALS_SIGN\n"
        with self.assertRaisesRegex(CollectorError, "Malformed KEY=VALUE line"):
            parse_raw_metrics(corrupted)

    def test_missing_required_key_raises_collector_error(self) -> None:
        """Verify omitting a required key raises CollectorError naming the missing key."""
        lines = [
            line
            for line in VALID_SAMPLE_STDOUT.splitlines()
            if not line.startswith("MEM_TOTAL_KB=")
        ]
        with self.assertRaisesRegex(CollectorError, "MEM_TOTAL_KB"):
            parse_raw_metrics("\n".join(lines))

    def test_non_integer_counter_raises_collector_error(self) -> None:
        """Verify non-numeric integer fields raise CollectorError."""
        bad_int = VALID_SAMPLE_STDOUT.replace(
            "RUNNING_PROCESSES=142", "RUNNING_PROCESSES=not_a_number"
        )
        with self.assertRaisesRegex(CollectorError, "Expected integer for RUNNING_PROCESSES"):
            parse_raw_metrics(bad_int)

    def test_invalid_network_status_raises_collector_error(self) -> None:
        """Verify an unknown NETWORK_STATUS string raises CollectorError."""
        bad_net = VALID_SAMPLE_STDOUT.replace(
            "NETWORK_STATUS=Connected", "NETWORK_STATUS=UnknownState"
        )
        with self.assertRaisesRegex(CollectorError, "Invalid NETWORK_STATUS"):
            parse_raw_metrics(bad_net)

    def test_missing_script_raises_collector_error(self) -> None:
        """Verify pointing to a directory without scripts raises CollectorError."""
        with self.assertRaisesRegex(CollectorError, "Bash script not found"):
            run_system_checks(bash_dir=self.temp_path)

    def test_non_executable_script_raises_collector_error(self) -> None:
        """Verify a script without execute permissions raises CollectorError."""
        if os.geteuid() == 0:
            self.skipTest("Root user bypasses execute permission checks.")
        script = self.temp_path / "system_checks.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        script.chmod(stat.S_IRUSR | stat.S_IWUSR)
        with self.assertRaisesRegex(CollectorError, "not executable"):
            run_system_checks(bash_dir=self.temp_path)

    def test_non_zero_script_exit_raises_collector_error_with_returncode(self) -> None:
        """Verify a failing Bash script raises CollectorError with returncode and stderr."""
        script = self.temp_path / "collect_metrics.sh"
        script.write_text(
            "#!/usr/bin/env bash\necho '[ERROR] Simulated proc failure' >&2\nexit 2\n",
            encoding="utf-8",
        )
        script.chmod(stat.S_IRWXU)
        with self.assertRaises(CollectorError) as ctx:
            collect_raw_metrics(bash_dir=self.temp_path)
        self.assertEqual(ctx.exception.returncode, 2)
        self.assertIn("Simulated proc failure", ctx.exception.stderr)

    def test_script_timeout_raises_collector_error(self) -> None:
        """Verify a hanging Bash script is terminated and raises CollectorError."""
        script = self.temp_path / "collect_metrics.sh"
        script.write_text("#!/usr/bin/env bash\nsleep 5\n", encoding="utf-8")
        script.chmod(stat.S_IRWXU)
        with self.assertRaisesRegex(CollectorError, "timed out"):
            collect_raw_metrics(bash_dir=self.temp_path, timeout=0.1)

    def test_live_integration_with_real_bash_scripts(self) -> None:
        """Verify collect_raw_metrics works end-to-end with the real bash/ scripts."""
        mock_ping = self.temp_path / "mock_ping"
        mock_ping.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        mock_ping.chmod(stat.S_IRWXU)

        overrides = {
            "LHM_CPU_SAMPLE_INTERVAL": "0.01",
            "LHM_PING_BIN": str(mock_ping),
        }
        run_system_checks(env_overrides=overrides)
        metrics = collect_raw_metrics(env_overrides=overrides)
        self.assertGreater(metrics.mem_total_kb, 0)
        self.assertGreater(metrics.disk_total_kb, 0)
        self.assertGreater(metrics.running_processes, 0)
        self.assertEqual(metrics.network_status, "Connected")


if __name__ == "__main__":
    unittest.main()