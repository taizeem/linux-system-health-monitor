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
from python.config import ThresholdConfig  # noqa: E402
from python.database import (  # noqa: E402
    DatabaseError,
    fetch_recent_history,
    fetch_summary_stats,
    init_database,
    save_evaluation,
)


class TestDatabaseModule(unittest.TestCase):
    """Verify SQLite schema creation, CRUD persistence, aggregations, and failure modes."""

    def setUp(self) -> None:
        """Create an isolated temporary directory and baseline evaluation fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_metrics.db"

        self.base_metrics = SystemMetrics(
            timestamp_utc="2026-11-14T06:00:00Z",
            cpu_usage_percent=40.0,
            ram_usage_percent=60.0,
            swap_usage_percent=10.0,
            disk_usage_percent=50.0,
            mem_total_kb=16000000,
            mem_used_kb=9600000,
            mem_available_kb=6400000,
            swap_total_kb=2000000,
            swap_used_kb=200000,
            disk_mount="/",
            disk_total_kb=100000000,
            disk_used_kb=50000000,
            disk_available_kb=50000000,
            running_processes=120,
            net_rx_bytes=1048576,
            net_tx_bytes=524288,
            net_rx_mb=1.0,
            net_tx_mb=0.5,
            network_status="Connected",
            is_network_connected=True,
        )
        self.thresholds = ThresholdConfig()

    def tearDown(self) -> None:
        """Restore file permissions and clean up temporary test directory."""
        for root, dirs, files in os.walk(self.temp_path):
            for name in dirs:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(root, name), stat.S_IRWXU)
        self.temp_dir.cleanup()

    def test_init_database_creates_file(self) -> None:
        """Verify init_database creates the SQLite file on disk."""
        returned_path = init_database(self.db_path)
        self.assertEqual(returned_path, self.db_path)
        self.assertTrue(self.db_path.is_file())

    def test_save_and_fetch_single_evaluation(self) -> None:
        """Verify saving a HealthEvaluation persists all fields and warnings accurately."""
        high_ram = replace(self.base_metrics, ram_usage_percent=75.0)
        evaluation = evaluate_thresholds(high_ram, self.thresholds)

        row_id = save_evaluation(evaluation, self.db_path)
        self.assertEqual(row_id, 1)

        history = fetch_recent_history(limit=5, db_path=self.db_path)
        self.assertEqual(len(history), 1)
        record = history[0]
        self.assertEqual(record["timestamp_utc"], "2026-11-14T06:00:00Z")
        self.assertEqual(record["ram_usage_percent"], 75.0)
        self.assertFalse(record["is_healthy"])
        self.assertEqual(record["warnings"], ("RAM usage is above 70%",))

    def test_fetch_recent_history_orders_newest_first_and_respects_limit(self) -> None:
        """Verify fetch_recent_history returns newest records first up to limit."""
        for idx in range(1, 5):
            m = replace(
                self.base_metrics,
                timestamp_utc=f"2026-11-14T06:0{idx}:00Z",
                cpu_usage_percent=float(idx * 10),
            )
            save_evaluation(evaluate_thresholds(m, self.thresholds), self.db_path)

        recent = fetch_recent_history(limit=2, db_path=self.db_path)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["cpu_usage_percent"], 40.0)
        self.assertEqual(recent[1]["cpu_usage_percent"], 30.0)

    def test_fetch_summary_stats_on_empty_database(self) -> None:
        """Verify fetch_summary_stats returns zeroes when no rows exist."""
        stats = fetch_summary_stats(self.db_path)
        self.assertEqual(stats["total_records"], 0)
        self.assertEqual(stats["avg_cpu_percent"], 0.0)
        self.assertEqual(stats["unhealthy_snapshots"], 0)

    def test_fetch_summary_stats_computes_accurate_aggregates(self) -> None:
        """Verify AVG, MAX, and unhealthy counts across multiple snapshots."""
        m1 = replace(self.base_metrics, cpu_usage_percent=20.0, ram_usage_percent=60.0)
        m2 = replace(self.base_metrics, cpu_usage_percent=60.0, ram_usage_percent=80.0)
        save_evaluation(evaluate_thresholds(m1, self.thresholds), self.db_path)
        save_evaluation(evaluate_thresholds(m2, self.thresholds), self.db_path)

        stats = fetch_summary_stats(self.db_path)
        self.assertEqual(stats["total_records"], 2)
        self.assertEqual(stats["avg_cpu_percent"], 40.0)
        self.assertEqual(stats["max_cpu_percent"], 60.0)
        self.assertEqual(stats["avg_ram_percent"], 70.0)
        self.assertEqual(stats["max_ram_percent"], 80.0)
        self.assertEqual(stats["unhealthy_snapshots"], 1)

    def test_invalid_limit_raises_database_error(self) -> None:
        """Verify non-positive or boolean limits raise DatabaseError."""
        with self.assertRaisesRegex(DatabaseError, "positive integer"):
            fetch_recent_history(limit=0, db_path=self.db_path)
        with self.assertRaisesRegex(DatabaseError, "positive integer"):
            fetch_recent_history(limit=True, db_path=self.db_path)  # type: ignore[arg-type]

    def test_missing_parent_directory_raises_database_error(self) -> None:
        """Verify pointing to a non-existent directory raises DatabaseError."""
        bad_path = self.temp_path / "non_existent_dir" / "metrics.db"
        with self.assertRaisesRegex(DatabaseError, "parent directory does not exist"):
            init_database(bad_path)

    def test_corrupted_database_file_raises_database_error(self) -> None:
        """Verify an invalid/corrupted SQLite file raises DatabaseError."""
        self.db_path.write_bytes(b"NOT A VALID SQLITE3 FILE HEADER" * 10)
        with self.assertRaisesRegex(DatabaseError, "SQLite error"):
            init_database(self.db_path)

    def test_read_only_database_directory_raises_database_error(self) -> None:
        """Verify permission error when creating DB in a read-only folder raises DatabaseError."""
        if os.geteuid() == 0:
            self.skipTest("Root user bypasses write permission checks.")
        locked_dir = self.temp_path / "locked_data"
        locked_dir.mkdir()
        locked_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        locked_db = locked_dir / "metrics.db"
        with self.assertRaisesRegex(DatabaseError, "SQLite error"):
            init_database(locked_db)


if __name__ == "__main__":
    unittest.main()
