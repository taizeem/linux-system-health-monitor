from dataclasses import dataclass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.collector import (  # noqa: E402
     VALID_NETWORK_STATUSES,
    CpuSample,
    RawSystemMetrics,
    collect_raw_metrics,
    run_system_checks,
)

BYTES_PER_MIB = 1024 * 1024


class AnalyzerError(ValueError):
    """Raised when raw metrics contain invalid capacities, process counts, or network data."""


@dataclass(frozen=True)
class SystemMetrics:
    """Processed system health metrics ready for threshold evaluation, storage, and reporting."""

    timestamp_utc: str
    cpu_usage_percent: float
    ram_usage_percent: float
    swap_usage_percent: float
    disk_usage_percent: float
    mem_total_kb: int
    mem_used_kb: int
    mem_available_kb: int
    swap_total_kb: int
    swap_used_kb: int
    disk_mount: str
    disk_total_kb: int
    disk_used_kb: int
    disk_available_kb: int
    running_processes: int
    net_rx_bytes: int
    net_tx_bytes: int
    net_rx_mb: float
    net_tx_mb: float
    network_status: str
    is_network_connected: bool


def _clamp_percentage(value: float) -> float:
    """Clamp a percentage value to [0.0, 100.0] rounded to 1 decimal place."""
    if value < 0.0:
        return 0.0
    if value > 100.0:
        return 100.0
    return round(value, 1)


def calculate_cpu_usage_percent(sample_1: CpuSample, sample_2: CpuSample) -> float:
    """Calculate live CPU usage percentage between two /proc/stat snapshots."""
    total_delta = sample_2.total - sample_1.total
    if total_delta <= 0:
        return 0.0

    idle_delta = sample_2.idle_all - sample_1.idle_all
    active_delta = total_delta - idle_delta
    usage = (active_delta / total_delta) * 100.0
    return _clamp_percentage(usage)


def calculate_ram_usage_percent(mem_total_kb: int, mem_available_kb: int) -> float:
    """Calculate RAM usage percentage using (MemTotal - MemAvailable) / MemTotal."""
    if mem_total_kb <= 0:
        raise AnalyzerError(f"mem_total_kb must be greater than 0, got {mem_total_kb}")

    used_kb = max(0, mem_total_kb - mem_available_kb)
    usage = (used_kb / mem_total_kb) * 100.0
    return _clamp_percentage(usage)


def calculate_swap_usage_percent(swap_total_kb: int, swap_free_kb: int) -> float:
    """Calculate Swap usage percentage; returns 0.0 if swap is disabled (swap_total_kb == 0)."""
    if swap_total_kb < 0:
        raise AnalyzerError(f"swap_total_kb cannot be negative, got {swap_total_kb}")
    if swap_total_kb == 0:
        return 0.0

    used_kb = max(0, swap_total_kb - swap_free_kb)
    usage = (used_kb / swap_total_kb) * 100.0
    return _clamp_percentage(usage)


def calculate_disk_usage_percent(disk_total_kb: int, disk_used_kb: int) -> float:
    """Calculate Disk usage percentage for the monitored mount point."""
    if disk_total_kb <= 0:
        raise AnalyzerError(f"disk_total_kb must be greater than 0, got {disk_total_kb}")

    clamped_used = max(0, disk_used_kb)
    usage = (clamped_used / disk_total_kb) * 100.0
    return _clamp_percentage(usage)


def validate_process_count(running_processes: int) -> int:
    """Validate that the running process count is a positive integer."""
    if running_processes <= 0:
        raise AnalyzerError(
            f"running_processes must be greater than 0, got {running_processes}"
        )
    return running_processes


def bytes_to_megabytes(byte_count: int) -> float:
    """Convert a non-negative byte counter into MiB rounded to 2 decimal places."""
    if byte_count < 0:
        raise AnalyzerError(f"Network byte counter cannot be negative, got {byte_count}")
    return round(byte_count / BYTES_PER_MIB, 2)


def evaluate_network_status(network_status: str) -> bool:
    """Validate network_status string and return True if Connected, False if Disconnected."""
    if network_status not in VALID_NETWORK_STATUSES:
        raise AnalyzerError(f"Invalid network_status: {network_status!r}")
    return network_status == "Connected"


def analyze_metrics(raw: RawSystemMetrics) -> SystemMetrics:
    """Convert a RawSystemMetrics reading into a processed SystemMetrics snapshot."""
    validated_procs = validate_process_count(raw.running_processes)
    is_connected = evaluate_network_status(raw.network_status)
    rx_mb = bytes_to_megabytes(raw.net_rx_bytes)
    tx_mb = bytes_to_megabytes(raw.net_tx_bytes)

    cpu_pct = calculate_cpu_usage_percent(raw.cpu_sample_1, raw.cpu_sample_2)
    ram_pct = calculate_ram_usage_percent(raw.mem_total_kb, raw.mem_available_kb)
    swap_pct = calculate_swap_usage_percent(raw.swap_total_kb, raw.swap_free_kb)
    disk_pct = calculate_disk_usage_percent(raw.disk_total_kb, raw.disk_used_kb)

    mem_used_kb = max(0, raw.mem_total_kb - raw.mem_available_kb)
    swap_used_kb = max(0, raw.swap_total_kb - raw.swap_free_kb)

    return SystemMetrics(
        timestamp_utc=raw.timestamp_utc,
        cpu_usage_percent=cpu_pct,
        ram_usage_percent=ram_pct,
        swap_usage_percent=swap_pct,
        disk_usage_percent=disk_pct,
        mem_total_kb=raw.mem_total_kb,
        mem_used_kb=mem_used_kb,
        mem_available_kb=raw.mem_available_kb,
        swap_total_kb=raw.swap_total_kb,
        swap_used_kb=swap_used_kb,
        disk_mount=raw.disk_mount,
        disk_total_kb=raw.disk_total_kb,
        disk_used_kb=raw.disk_used_kb,
        disk_available_kb=raw.disk_available_kb,
        running_processes=validated_procs,
        net_rx_bytes=raw.net_rx_bytes,
        net_tx_bytes=raw.net_tx_bytes,
        net_rx_mb=rx_mb,
        net_tx_mb=tx_mb,
        network_status=raw.network_status,
        is_network_connected=is_connected,
    )


if __name__ == "__main__":
    run_system_checks()
    raw_metrics = collect_raw_metrics()
    analyzed = analyze_metrics(raw_metrics)
    print(f"========== ANALYZED METRICS ({analyzed.timestamp_utc}) ==========")
    print(f"CPU Usage:         {analyzed.cpu_usage_percent:.1f}%")
    print(f"RAM Usage:         {analyzed.ram_usage_percent:.1f}%")
    print(f"Swap Usage:        {analyzed.swap_usage_percent:.1f}%")
    print(f"Disk Usage ({analyzed.disk_mount}):    {analyzed.disk_usage_percent:.1f}%")
    print(f"Running Processes: {analyzed.running_processes}")
    print(
        f"Network:           {analyzed.network_status} "
        f"(RX: {analyzed.net_rx_mb:.2f} MiB, TX: {analyzed.net_tx_mb:.2f} MiB)"
    )
