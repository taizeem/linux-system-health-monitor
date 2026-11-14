import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import HealthEvaluation  # noqa: E402

REPORT_HEADER = "========== SYSTEM HEALTH =========="
REPORT_FOOTER = "==================================="
HISTORY_HEADER = "======= HISTORICAL SUMMARY ========"
LABEL_WIDTH = 20

MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MiB per log file
LOG_BACKUP_COUNT = 3


def get_default_app_log_path() -> Path:
    """Return the application log path from LHM_APP_LOG_FILE or logs/app.log."""
    env_log = os.environ.get("LHM_APP_LOG_FILE")
    if env_log:
        return Path(env_log)
    return PROJECT_ROOT / "logs" / "app.log"


def configure_logger(log_path: Optional[Path] = None) -> logging.Logger:
    """Configure and return a rotating file logger for the health monitor."""
    target_path = log_path or get_default_app_log_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("linux_health_monitor")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Close and remove existing handlers so repeated test runs do not leak FDs
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    file_handler = RotatingFileHandler(
        filename=str(target_path),
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        fmt="%(asctime)sZ [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    import time
    formatter.converter = time.gmtime
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _format_line(label: str, value: str) -> str:
    """Format a single aligned key-value metric row."""
    return f"{label.ljust(LABEL_WIDTH)}{value}"


def format_health_report(evaluation: HealthEvaluation) -> str:
    """Format a HealthEvaluation into the standard CLI system health report."""
    m = evaluation.metrics
    lines = [
        REPORT_HEADER,
        "",
        _format_line("CPU Usage:", f"{m.cpu_usage_percent:g}%"),
        _format_line("RAM Usage:", f"{m.ram_usage_percent:g}%"),
        _format_line("Swap Usage:", f"{m.swap_usage_percent:g}%"),
        _format_line("Disk Usage:", f"{m.disk_usage_percent:g}%"),
        _format_line("Running Processes:", f"{m.running_processes}"),
        _format_line("Network:", f"{m.network_status}"),
        "",
    ]

    for warning in evaluation.warnings:
        lines.append(f"[WARNING] {warning}")

    lines.append(f"[STATUS] {evaluation.status_message}")
    lines.append("")
    lines.append(REPORT_FOOTER)
    return "\n".join(lines)


def format_history_report(
    records: Sequence[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    """Format SQLite summary statistics and recent snapshots into a CLI report."""
    lines = [
        HISTORY_HEADER,
        "",
        _format_line("Total Snapshots:", f"{summary['total_records']}"),
        _format_line("Avg / Max CPU:", f"{summary['avg_cpu_percent']:g}% / {summary['max_cpu_percent']:g}%"),
        _format_line("Avg / Max RAM:", f"{summary['avg_ram_percent']:g}% / {summary['max_ram_percent']:g}%"),
        _format_line("Max Disk Usage:", f"{summary['max_disk_percent']:g}%"),
        _format_line("Warning Snapshots:", f"{summary['unhealthy_snapshots']}"),
        "",
    ]

    if not records:
        lines.append("No historical snapshots recorded yet.")
    else:
        lines.append("Recent Snapshots (Newest First):")
        for rec in records:
            status_tag = "OK" if rec["is_healthy"] else "WARN"
            lines.append(
                f"  [{rec['timestamp_utc']}] "
                f"CPU={rec['cpu_usage_percent']:g}% "
                f"RAM={rec['ram_usage_percent']:g}% "
                f"Swap={rec['swap_usage_percent']:g}% "
                f"Disk={rec['disk_usage_percent']:g}% "
                f"Procs={rec['running_processes']} "
                f"Net={rec['network_status']} "
                f"({status_tag})"
            )

    lines.append("")
    lines.append(REPORT_FOOTER)
    return "\n".join(lines)


def log_evaluation(logger: logging.Logger, evaluation: HealthEvaluation) -> None:
    """Record a health evaluation and any threshold warnings to the application log."""
    m = evaluation.metrics
    logger.info(
        "Snapshot collected: CPU=%.1f%% RAM=%.1f%% Swap=%.1f%% Disk=%.1f%% Procs=%d Net=%s",
        m.cpu_usage_percent,
        m.ram_usage_percent,
        m.swap_usage_percent,
        m.disk_usage_percent,
        m.running_processes,
        m.network_status,
    )
    for warning in evaluation.warnings:
        logger.warning("Threshold exceeded: %s", warning)
