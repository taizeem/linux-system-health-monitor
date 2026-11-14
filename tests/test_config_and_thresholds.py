from dataclasses import replace
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import SystemMetrics, evaluate_thresholds  # noqa: E402
from python.config import (  # noqa: E402
    ConfigError,
    ThresholdConfig,
    load_threshold_config,
)


class TestConfigAndThresholds(unittest.TestCase):
    """Test configuration loading, validation, and resource threshold detection."""

    def setUp(self) -> None:
        """Create temporary directory and baseline healthy SystemMetrics fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.healthy_metrics = SystemMetrics(
            timestamp_utc="2026-11-14T04:15:00Z",
            cpu_usage_percent=45.0,
            ram_usage_percent=60.0,
            swap_usage_percent=12.0,
            disk_usage_percent=68.0,
            mem_total_kb=16000000,
            mem_used_kb=9600000,
            mem_available_kb=6400000,
            swap_total_kb=2000000,
            swap_used_kb=240000,
            disk_mount="/",
            disk_total_kb=100000000,
            disk_used_kb=68000000,
            disk_available_kb=32000000,
            running_processes=124,
            net_rx_bytes=1048576,
            net_tx_bytes=524288,
            net_rx_mb=1.0,
            net_tx_mb=0.5,
            network_status="Connected",
            is_network_connected=True,
        )

    def tearDown(self) -> None:
        """Restore file permissions and remove temporary directory."""
        for root, dirs, files in os.walk(self.temp_path):
            for name in dirs:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
        self.temp_dir.cleanup()

    def test_load_default_project_thresholds_json(self) -> None:
        """Verify config/thresholds.json loads cleanly with expected defaults."""
        cfg = load_threshold_config(PROJECT_ROOT / "config" / "thresholds.json")
        self.assertEqual(cfg.cpu_percent, 80.0)
        self.assertEqual(cfg.ram_percent, 70.0)
        self.assertEqual(cfg.swap_percent, 50.0)
        self.assertEqual(cfg.disk_percent, 85.0)
        self.assertEqual(cfg.max_processes, 500)
        self.assertTrue(cfg.require_network)

    def test_missing_config_file_raises_config_error(self) -> None:
        """Verify missing configuration file raises ConfigError."""
        missing_path = self.temp_path / "does_not_exist.json"
        with self.assertRaisesRegex(ConfigError, "Configuration file not found"):
            load_threshold_config(missing_path)

    def test_unreadable_config_file_raises_config_error(self) -> None:
        """Verify permission-denied config file raises ConfigError."""
        if os.geteuid() == 0:
            self.skipTest("Root user bypasses read permission checks.")
        locked_file = self.temp_path / "locked.json"
        locked_file.write_text('{"cpu_percent": 75.0}', encoding="utf-8")
        locked_file.chmod(0o000)
        with self.assertRaisesRegex(ConfigError, "Permission denied"):
            load_threshold_config(locked_file)

    def test_malformed_json_raises_config_error(self) -> None:
        """Verify invalid JSON syntax raises ConfigError."""
        bad_json = self.temp_path / "bad.json"
        bad_json.write_text("{cpu_percent: 80.0", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Malformed JSON"):
            load_threshold_config(bad_json)

    def test_non_object_json_raises_config_error(self) -> None:
        """Verify a JSON list instead of a JSON object raises ConfigError."""
        list_json = self.temp_path / "list.json"
        list_json.write_text("[80.0, 70.0]", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "Expected JSON object"):
            load_threshold_config(list_json)

    def test_unknown_config_key_raises_config_error(self) -> None:
        """Verify misspelled or unknown JSON keys are rejected."""
        unknown_json = self.temp_path / "unknown.json"
        unknown_json.write_text('{"cpu_percentage_typo": 80.0}', encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "cpu_percentage_typo"):
            load_threshold_config(unknown_json)

    def test_boolean_passed_for_numeric_threshold_raises_config_error(self) -> None:
        """Verify Python bool subclass of int is rejected for numeric fields."""
        with self.assertRaisesRegex(ConfigError, "must be a number"):
            ThresholdConfig(cpu_percent=True)  # type: ignore[arg-type]

    def test_out_of_range_percentage_raises_config_error(self) -> None:
        """Verify percentages <= 0 or > 100 raise ConfigError."""
        with self.assertRaisesRegex(ConfigError, "between 0.0"):
            ThresholdConfig(ram_percent=0.0)
        with self.assertRaisesRegex(ConfigError, "between 0.0"):
            ThresholdConfig(disk_percent=105.0)

    def test_non_positive_max_processes_raises_config_error(self) -> None:
        """Verify max_processes <= 0 raises ConfigError."""
        with self.assertRaisesRegex(ConfigError, "must be greater than 0"):
            ThresholdConfig(max_processes=0)

    def test_all_metrics_within_thresholds_is_healthy(self) -> None:
        """Verify no warnings are produced when all metrics are at or below limits."""
        evaluation = evaluate_thresholds(self.healthy_metrics)
        self.assertTrue(evaluation.is_healthy)
        self.assertEqual(evaluation.warnings, ())
        self.assertEqual(evaluation.status_message, "System is operational")

    def test_exact_boundary_equality_does_not_trigger_warning(self) -> None:
        """Verify usage exactly equal to threshold (70.0 == 70.0) does not warn."""
        at_limit = replace(self.healthy_metrics, ram_usage_percent=70.0)
        evaluation = evaluate_thresholds(at_limit, ThresholdConfig(ram_percent=70.0))
        self.assertTrue(evaluation.is_healthy)
        self.assertEqual(evaluation.warnings, ())

    def test_ram_exceeding_threshold_triggers_exact_warning(self) -> None:
        """Verify RAM at 72.0% with 70% threshold matches specification warning text."""
        ram_high = replace(self.healthy_metrics, ram_usage_percent=72.0)
        evaluation = evaluate_thresholds(ram_high, ThresholdConfig(ram_percent=70.0))
        self.assertFalse(evaluation.is_healthy)
        self.assertEqual(evaluation.warnings, ("RAM usage is above 70%",))

    def test_multiple_simultaneous_threshold_violations(self) -> None:
        """Verify all violated resources generate warnings in deterministic order."""
        overloaded = replace(
            self.healthy_metrics,
            cpu_usage_percent=95.0,
            ram_usage_percent=85.0,
            swap_usage_percent=60.0,
            disk_usage_percent=90.0,
            running_processes=650,
            network_status="Disconnected",
            is_network_connected=False,
        )
        evaluation = evaluate_thresholds(overloaded)
        self.assertFalse(evaluation.is_healthy)
        self.assertEqual(
            evaluation.warnings,
            (
                "CPU usage is above 80%",
                "RAM usage is above 70%",
                "Swap usage is above 50%",
                "Disk usage is above 85%",
                "Running processes count is above 500",
                "Network is disconnected",
            ),
        )

    def test_disconnected_network_ignored_when_require_network_is_false(self) -> None:
        """Verify offline systems do not warn if require_network is configured False."""
        offline = replace(
            self.healthy_metrics,
            network_status="Disconnected",
            is_network_connected=False,
        )
        cfg = ThresholdConfig(require_network=False)
        evaluation = evaluate_thresholds(offline, cfg)
        self.assertTrue(evaluation.is_healthy)
        self.assertEqual(evaluation.warnings, ())


if __name__ == "__main__":
    unittest.main()
