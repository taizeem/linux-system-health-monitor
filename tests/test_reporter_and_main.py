from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import SystemMetrics, evaluate_thresholds  # noqa: E402
from python.config import ThresholdConfig  # noqa: E402
from python.database import fetch_recent_history  # noqa: E402
from python.main import (  # noqa: E402
    EXIT_CONFIG_ERROR,
    EXIT_DATABASE_ERROR,
    EXIT_OK,
    main,
)
from python.reporter import (  # noqa: E402
    configure_logger,
    format_health_report,
    format_history_report,
    log_evaluation,
)


EXPECTED_SPEC_REPORT = """\
========== SYSTEM HEALTH ==========

CPU Usage:          45%
RAM Usage:          72%
Swap Usage:         12%
Disk Usage:         68%
Running Processes:  124
Network:            Connected

[WARNING] RAM usage is above 70%
[STATUS] System is operational

==================================="""


class TestReporterAndMain(unittest.TestCase):
    """Verify report formatting, rotating file logging, and CLI execution modes."""

    def setUp(self) -> None:
        """Create temporary paths and a sample SystemMetrics matching the project spec."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "metrics.db"
        self.log_path = self.temp_path / "app.log"
        self.config_path = self.temp_path / "thresholds.json"
        self.config_path.write_text(
            '{"cpu_percent": 80.0, "ram_percent": 70.0, "swap_percent": 50.0, '
            '"disk_percent": 85.0, "max_processes": 500, "require_network": true}',
            encoding="utf-8",
        )

        self.spec_metrics = SystemMetrics(
            timestamp_utc="2026-11-14T06:30:00Z",
            cpu_usage_percent=45.0,
            ram_usage_percent=72.0,
            swap_usage_percent=12.0,
            disk_usage_percent=68.0,
            mem_total_kb=16000000,
            mem_used_kb=11520000,
            mem_available_kb=4480000,
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
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_format_health_report_matches_specification_verbatim(self) -> None:
        """Verify format_health_report matches the exact project example output."""
        evaluation = evaluate_thresholds(self.spec_metrics, ThresholdConfig())
        report = format_health_report(evaluation)
        self.assertEqual(report, EXPECTED_SPEC_REPORT)

    def test_format_health_report_without_warnings(self) -> None:
        """Verify healthy report omits [WARNING] lines."""
        healthy = replace(self.spec_metrics, ram_usage_percent=55.0)
        evaluation = evaluate_thresholds(healthy, ThresholdConfig())
        report = format_health_report(evaluation)
        self.assertNotIn("[WARNING]", report)
        self.assertIn("[STATUS] System is operational", report)

    def test_log_evaluation_writes_info_and_warning_entries(self) -> None:
        """Verify log_evaluation writes snapshot summary and warnings to app.log."""
        logger = configure_logger(self.log_path)
        evaluation = evaluate_thresholds(self.spec_metrics, ThresholdConfig())
        log_evaluation(logger, evaluation)

        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn("[INFO] Snapshot collected:", log_text)
        self.assertIn("[WARNING] Threshold exceeded: RAM usage is above 70%", log_text)

    def test_format_history_report_with_empty_and_populated_data(self) -> None:
        """Verify format_history_report renders summary stats and snapshot rows."""
        empty_summary = {
            "total_records": 0,
            "avg_cpu_percent": 0.0,
            "max_cpu_percent": 0.0,
            "avg_ram_percent": 0.0,
            "max_ram_percent": 0.0,
            "max_disk_percent": 0.0,
            "unhealthy_snapshots": 0,
        }
        empty_report = format_history_report([], empty_summary)
        self.assertIn("No historical snapshots recorded yet.", empty_report)

    def test_main_cli_end_to_end_saves_and_prints_report(self) -> None:
        """Verify main() collects metrics, writes to SQLite, and prints the report."""
        mock_ping = self.temp_path / "mock_ping"
        mock_ping.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        mock_ping.chmod(stat.S_IRWXU)

        stdout_buf = io.StringIO()
        with patch.dict(
            os.environ,
            {"LHM_CPU_SAMPLE_INTERVAL": "0.01", "LHM_PING_BIN": str(mock_ping)},
        ):
            with redirect_stdout(stdout_buf):
                rc = main(
                    [
                        "--config",
                        str(self.config_path),
                        "--db",
                        str(self.db_path),
                        "--log-file",
                        str(self.log_path),
                    ]
                )

        self.assertEqual(rc, EXIT_OK)
        self.assertIn("========== SYSTEM HEALTH ==========", stdout_buf.getvalue())
        records = fetch_recent_history(limit=5, db_path=self.db_path)
        self.assertEqual(len(records), 1)

    def test_main_cli_quiet_and_history_flags(self) -> None:
        """Verify --quiet suppresses stdout and --history prints stored records."""
        mock_ping = self.temp_path / "mock_ping"
        mock_ping.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        mock_ping.chmod(stat.S_IRWXU)

        quiet_buf = io.StringIO()
        with patch.dict(
            os.environ,
            {"LHM_CPU_SAMPLE_INTERVAL": "0.01", "LHM_PING_BIN": str(mock_ping)},
        ):
            with redirect_stdout(quiet_buf):
                rc = main(
                    [
                        "--quiet",
                        "--config",
                        str(self.config_path),
                        "--db",
                        str(self.db_path),
                        "--log-file",
                        str(self.log_path),
                    ]
                )
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(quiet_buf.getvalue().strip(), "")

        hist_buf = io.StringIO()
        with redirect_stdout(hist_buf):
            rc_hist = main(
                [
                    "--history",
                    "5",
                    "--db",
                    str(self.db_path),
                    "--log-file",
                    str(self.log_path),
                ]
            )
        self.assertEqual(rc_hist, EXIT_OK)
        self.assertIn("======= HISTORICAL SUMMARY ========", hist_buf.getvalue())

    def test_main_cli_returns_exit_code_3_on_missing_config(self) -> None:
        """Verify main() returns EXIT_CONFIG_ERROR (3) when --config file is missing."""
        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            rc = main(
                [
                    "--config",
                    str(self.temp_path / "missing.json"),
                    "--log-file",
                    str(self.log_path),
                ]
            )
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        self.assertIn("Configuration failure", stderr_buf.getvalue())

    def test_main_cli_returns_exit_code_5_on_invalid_history_limit(self) -> None:
        """Verify main() returns EXIT_DATABASE_ERROR (5) when --history limit <= 0."""
        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf):
            rc = main(
                [
                    "--history",
                    "0",
                    "--db",
                    str(self.db_path),
                    "--log-file",
                    str(self.log_path),
                ]
            )
        self.assertEqual(rc, EXIT_DATABASE_ERROR)
        self.assertIn("Database failure", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
