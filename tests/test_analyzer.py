from dataclasses import replace
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import (  # noqa: E402
    AnalyzerError,
    analyze_metrics,
    calculate_cpu_usage_percent,
    calculate_disk_usage_percent,
    calculate_ram_usage_percent,
    calculate_swap_usage_percent,
)
from python.collector import CpuSample, RawSystemMetrics  # noqa: E402


class TestAnalyzerModule(unittest.TestCase):
    """Verify CPU, RAM, Swap, and Disk utilization formulas and edge cases."""

    def setUp(self) -> None:
        """Create a baseline valid RawSystemMetrics fixture for testing."""
        self.sample_1 = CpuSample(
            user=1000, nice=0, system=500, idle=8000, iowait=500, irq=0, softirq=0, steal=0
        )
        # Delta: total = +200 (10200 - 10000), idle_all = +110 (8610 - 8500) -> active = 90 -> 45.0%
        self.sample_2 = CpuSample(
            user=1060, nice=0, system=530, idle=8100, iowait=510, irq=0, softirq=0, steal=0
        )
        self.raw = RawSystemMetrics(
            timestamp_utc="2026-11-14T04:00:00Z",
            cpu_sample_1=self.sample_1,
            cpu_sample_2=self.sample_2,
            mem_total_kb=1000000,
            mem_free_kb=100000,
            mem_available_kb=280000,  # 72% used
            mem_buffers_kb=80000,
            mem_cached_kb=100000,
            swap_total_kb=1000000,
            swap_free_kb=880000,      # 12% used
            disk_mount="/",
            disk_total_kb=5000000,
            disk_used_kb=3400000,     # 68% used
            disk_available_kb=1600000,
            running_processes=124,
            net_rx_bytes=102400,
            net_tx_bytes=51200,
            network_status="Connected",
        )

    def test_analyze_metrics_computes_expected_percentages(self) -> None:
        """Verify end-to-end analyze_metrics produces exact expected percentages."""
        result = analyze_metrics(self.raw)
        self.assertEqual(result.cpu_usage_percent, 45.0)
        self.assertEqual(result.ram_usage_percent, 72.0)
        self.assertEqual(result.swap_usage_percent, 12.0)
        self.assertEqual(result.disk_usage_percent, 68.0)
        self.assertEqual(result.mem_used_kb, 720000)
        self.assertEqual(result.swap_used_kb, 120000)
        self.assertEqual(result.running_processes, 124)
        self.assertEqual(result.network_status, "Connected")

    def test_cpu_zero_delta_returns_zero_percent(self) -> None:
        """Verify identical CPU snapshots return 0.0% without ZeroDivisionError."""
        self.assertEqual(calculate_cpu_usage_percent(self.sample_1, self.sample_1), 0.0)

    def test_cpu_negative_delta_returns_zero_percent(self) -> None:
        """Verify reversed CPU snapshots safely return 0.0%."""
        self.assertEqual(calculate_cpu_usage_percent(self.sample_2, self.sample_1), 0.0)

    def test_cpu_100_percent_busy_when_idle_unchanged(self) -> None:
        """Verify CPU usage is 100.0% when only user/system jiffies increase."""
        busy_sample_2 = replace(self.sample_1, user=1500, system=700)
        self.assertEqual(calculate_cpu_usage_percent(self.sample_1, busy_sample_2), 100.0)

    def test_ram_usage_zero_total_raises_analyzer_error(self) -> None:
        """Verify mem_total_kb <= 0 raises AnalyzerError."""
        with self.assertRaisesRegex(AnalyzerError, "mem_total_kb must be greater than 0"):
            calculate_ram_usage_percent(0, 0)

    def test_ram_available_exceeding_total_clamps_to_zero(self) -> None:
        """Verify mem_available_kb > mem_total_kb clamps safely to 0.0%."""
        self.assertEqual(calculate_ram_usage_percent(1000, 1200), 0.0)

    def test_swap_disabled_returns_zero_percent(self) -> None:
        """Verify swap_total_kb == 0 returns 0.0% rather than raising ZeroDivisionError."""
        self.assertEqual(calculate_swap_usage_percent(0, 0), 0.0)

    def test_swap_negative_total_raises_analyzer_error(self) -> None:
        """Verify negative swap_total_kb raises AnalyzerError."""
        with self.assertRaisesRegex(AnalyzerError, "swap_total_kb cannot be negative"):
            calculate_swap_usage_percent(-100, 0)

    def test_disk_usage_zero_total_raises_analyzer_error(self) -> None:
        """Verify disk_total_kb <= 0 raises AnalyzerError."""
        with self.assertRaisesRegex(AnalyzerError, "disk_total_kb must be greater than 0"):
            calculate_disk_usage_percent(0, 100)

    def test_disk_used_exceeding_total_clamps_to_100_percent(self) -> None:
        """Verify disk_used_kb > disk_total_kb clamps to 100.0%."""
        self.assertEqual(calculate_disk_usage_percent(1000, 1050), 100.0)

    def test_zero_running_processes_raises_analyzer_error(self) -> None:
        """Verify running_processes <= 0 raises AnalyzerError."""
        invalid_raw = replace(self.raw, running_processes=0)
        with self.assertRaisesRegex(AnalyzerError, "running_processes must be greater than 0"):
            analyze_metrics(invalid_raw)


if __name__ == "__main__":
    unittest.main()