from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import (  # noqa: E402
    HealthEvaluation,
    analyze_metrics,
    evaluate_thresholds,
)
from python.collector import collect_raw_metrics, run_system_checks  # noqa: E402
from python.config import load_threshold_config  # noqa: E402

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS metric_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    cpu_usage_percent REAL NOT NULL,
    ram_usage_percent REAL NOT NULL,
    swap_usage_percent REAL NOT NULL,
    disk_usage_percent REAL NOT NULL,
    mem_total_kb INTEGER NOT NULL,
    mem_used_kb INTEGER NOT NULL,
    mem_available_kb INTEGER NOT NULL,
    swap_total_kb INTEGER NOT NULL,
    swap_used_kb INTEGER NOT NULL,
    disk_mount TEXT NOT NULL,
    disk_total_kb INTEGER NOT NULL,
    disk_used_kb INTEGER NOT NULL,
    disk_available_kb INTEGER NOT NULL,
    running_processes INTEGER NOT NULL,
    net_rx_bytes INTEGER NOT NULL,
    net_tx_bytes INTEGER NOT NULL,
    net_rx_mb REAL NOT NULL,
    net_tx_mb REAL NOT NULL,
    network_status TEXT NOT NULL,
    is_healthy INTEGER NOT NULL,
    warnings_json TEXT NOT NULL
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_metric_history_timestamp
ON metric_history (timestamp_utc);
"""

INSERT_METRIC_SQL = """
INSERT INTO metric_history (
    timestamp_utc,
    cpu_usage_percent,
    ram_usage_percent,
    swap_usage_percent,
    disk_usage_percent,
    mem_total_kb,
    mem_used_kb,
    mem_available_kb,
    swap_total_kb,
    swap_used_kb,
    disk_mount,
    disk_total_kb,
    disk_used_kb,
    disk_available_kb,
    running_processes,
    net_rx_bytes,
    net_tx_bytes,
    net_rx_mb,
    net_tx_mb,
    network_status,
    is_healthy,
    warnings_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class DatabaseError(RuntimeError):
    """Raised when a SQLite operation fails due to I/O, permission, or corruption errors."""


def get_default_db_path() -> Path:
    """Return the SQLite database path from LHM_DB_FILE env var or data/metrics.db."""
    env_db = os.environ.get("LHM_DB_FILE")
    if env_db:
        return Path(env_db)
    return PROJECT_ROOT / "data" / "metrics.db"


@contextmanager
def _connect_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with row_factory and automatic commit/close handling."""
    conn: Optional[sqlite3.Connection] = None
    try:
        if not db_path.parent.exists():
            raise DatabaseError(f"Database parent directory does not exist: {db_path.parent}")
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
        raise DatabaseError(f"SQLite error on {db_path}: {exc}") from exc
    except OSError as exc:
        raise DatabaseError(f"OS error accessing database {db_path}: {exc}") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def init_database(db_path: Optional[Path] = None) -> Path:
    """Initialize the metric_history table, index, and WAL mode."""
    target_path = db_path or get_default_db_path()
    with _connect_db(target_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_INDEX_SQL)
    return target_path


def save_evaluation(
    evaluation: HealthEvaluation,
    db_path: Optional[Path] = None,
) -> int:
    """Insert a HealthEvaluation record into SQLite and return the inserted row id."""
    target_path = init_database(db_path)
    metrics = evaluation.metrics
    warnings_serialized = json.dumps(list(evaluation.warnings))

    params = (
        metrics.timestamp_utc,
        metrics.cpu_usage_percent,
        metrics.ram_usage_percent,
        metrics.swap_usage_percent,
        metrics.disk_usage_percent,
        metrics.mem_total_kb,
        metrics.mem_used_kb,
        metrics.mem_available_kb,
        metrics.swap_total_kb,
        metrics.swap_used_kb,
        metrics.disk_mount,
        metrics.disk_total_kb,
        metrics.disk_used_kb,
        metrics.disk_available_kb,
        metrics.running_processes,
        metrics.net_rx_bytes,
        metrics.net_tx_bytes,
        metrics.net_rx_mb,
        metrics.net_tx_mb,
        metrics.network_status,
        1 if evaluation.is_healthy else 0,
        warnings_serialized,
    )

    with _connect_db(target_path) as conn:
        cursor = conn.execute(INSERT_METRIC_SQL, params)
        row_id = cursor.lastrowid
        if row_id is None:
            raise DatabaseError("Failed to retrieve inserted row ID from SQLite.")
        return int(row_id)


def fetch_recent_history(
    limit: int = 10,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Fetch the most recent metric records ordered from newest to oldest."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise DatabaseError(f"History limit must be a positive integer, got {limit!r}")

    target_path = init_database(db_path)
    query = """
    SELECT * FROM metric_history
    ORDER BY id DESC
    LIMIT ?;
    """
    with _connect_db(target_path) as conn:
        rows = conn.execute(query, (limit,)).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["is_healthy"] = bool(record["is_healthy"])
        record["warnings"] = tuple(json.loads(record.pop("warnings_json")))
        results.append(record)
    return results


def fetch_summary_stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Compute aggregate statistics across all stored historical metric snapshots."""
    target_path = init_database(db_path)
    query = """
    SELECT
        COUNT(*) AS total_records,
        ROUND(AVG(cpu_usage_percent), 1) AS avg_cpu_percent,
        ROUND(MAX(cpu_usage_percent), 1) AS max_cpu_percent,
        ROUND(AVG(ram_usage_percent), 1) AS avg_ram_percent,
        ROUND(MAX(ram_usage_percent), 1) AS max_ram_percent,
        ROUND(MAX(disk_usage_percent), 1) AS max_disk_percent,
        SUM(CASE WHEN is_healthy = 0 THEN 1 ELSE 0 END) AS unhealthy_snapshots
    FROM metric_history;
    """
    with _connect_db(target_path) as conn:
        row = conn.execute(query).fetchone()

    if row is None or row["total_records"] == 0:
        return {
            "total_records": 0,
            "avg_cpu_percent": 0.0,
            "max_cpu_percent": 0.0,
            "avg_ram_percent": 0.0,
            "max_ram_percent": 0.0,
            "max_disk_percent": 0.0,
            "unhealthy_snapshots": 0,
        }

    return {
        "total_records": int(row["total_records"]),
        "avg_cpu_percent": float(row["avg_cpu_percent"] or 0.0),
        "max_cpu_percent": float(row["max_cpu_percent"] or 0.0),
        "avg_ram_percent": float(row["avg_ram_percent"] or 0.0),
        "max_ram_percent": float(row["max_ram_percent"] or 0.0),
        "max_disk_percent": float(row["max_disk_percent"] or 0.0),
        "unhealthy_snapshots": int(row["unhealthy_snapshots"] or 0),
    }


if __name__ == "__main__":
    run_system_checks()
    cfg = load_threshold_config()
    raw = collect_raw_metrics()
    metrics = analyze_metrics(raw)
    evaluation = evaluate_thresholds(metrics, cfg)
    inserted_id = save_evaluation(evaluation)
    summary = fetch_summary_stats()
    print(f"Saved snapshot #{inserted_id} at {metrics.timestamp_utc} to {get_default_db_path()}")
    print(f"Database Summary: {summary}")
