from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Dict, Mapping, Optional


# Compiled regex matching strict KEY=VALUE lines (uppercase alphanumeric/underscore keys)
KEY_VALUE_PATTERN = re.compile(r"^([A-Z0-9_]+)=(.+)$")

DEFAULT_TIMEOUT_SECONDS = 15.0


class CollectorError(RuntimeError):
    """Raised when a Bash monitoring script fails, times out, or emits invalid data."""

    def __init__(
        self,
        message: str,
        returncode: Optional[int] = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class CpuSample:
    """Represents a single point-in-time snapshot of aggregate CPU jiffies from /proc/stat."""

    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int

    @property
    def idle_all(self) -> int:
        """Return total idle jiffies (idle + iowait)."""
        return self.idle + self.iowait

    @property
    def total(self) -> int:
        """Return sum of all CPU jiffies across all states."""
        return (
            self.user
            + self.nice
            + self.system
            + self.idle
            + self.iowait
            + self.irq
            + self.softirq
            + self.steal
        )


@dataclass(frozen=True)
class RawSystemMetrics:
    """Strongly typed, immutable container for all raw metrics emitted by collect_metrics.sh."""

    timestamp_utc: str
    cpu_sample_1: CpuSample
    cpu_sample_2: CpuSample
    mem_total_kb: int
    mem_free_kb: int
    mem_available_kb: int
    mem_buffers_kb: int
    mem_cached_kb: int
    swap_total_kb: int
    swap_free_kb: int
    disk_mount: str
    disk_total_kb: int
    disk_used_kb: int
    disk_available_kb: int
    running_processes: int
    net_rx_bytes: int
    net_tx_bytes: int
    network_status: str


REQUIRED_KEYS = frozenset(
    {
        "TIMESTAMP_UTC",
        "CPU1_USER",
        "CPU1_NICE",
        "CPU1_SYSTEM",
        "CPU1_IDLE",
        "CPU1_IOWAIT",
        "CPU1_IRQ",
        "CPU1_SOFTIRQ",
        "CPU1_STEAL",
        "CPU2_USER",
        "CPU2_NICE",
        "CPU2_SYSTEM",
        "CPU2_IDLE",
        "CPU2_IOWAIT",
        "CPU2_IRQ",
        "CPU2_SOFTIRQ",
        "CPU2_STEAL",
        "MEM_TOTAL_KB",
        "MEM_FREE_KB",
        "MEM_AVAILABLE_KB",
        "MEM_BUFFERS_KB",
        "MEM_CACHED_KB",
        "SWAP_TOTAL_KB",
        "SWAP_FREE_KB",
        "DISK_MOUNT",
        "DISK_TOTAL_KB",
        "DISK_USED_KB",
        "DISK_AVAILABLE_KB",
        "RUNNING_PROCESSES",
        "NET_RX_BYTES",
        "NET_TX_BYTES",
        "NETWORK_STATUS",
    }
)

VALID_NETWORK_STATUSES = frozenset({"Connected", "Disconnected"})


def get_default_bash_dir() -> Path:
    """Return the absolute path to the repository's bash/ directory."""
    return Path(__file__).resolve().parent.parent / "bash"


def _run_bash_script(
    script_path: Path,
    args: Optional[list[str]] = None,
    env_overrides: Optional[Mapping[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Execute a Bash script safely and return its stdout or raise CollectorError."""
    if not script_path.is_file():
        raise CollectorError(f"Bash script not found: {script_path}")
    if not os.access(script_path, os.X_OK):
        raise CollectorError(f"Bash script is not executable: {script_path}")

    cmd = [str(script_path)]
    if args:
        cmd.extend(args)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(
            f"Bash script timed out after {timeout}s: {script_path.name}"
        ) from exc
    except OSError as exc:
        raise CollectorError(
            f"OS error executing {script_path.name}: {exc}"
        ) from exc

    if completed.returncode != 0:
        stderr_clean = (completed.stderr or "").strip()
        raise CollectorError(
            f"Script {script_path.name} failed with exit code {completed.returncode}: {stderr_clean}",
            returncode=completed.returncode,
            stderr=stderr_clean,
        )

    return completed.stdout


def parse_key_value_output(raw_stdout: str) -> Dict[str, str]:
    """Parse and validate KEY=VALUE lines from collect_metrics.sh stdout."""
    if not raw_stdout or not raw_stdout.strip():
        raise CollectorError("Collector script produced empty stdout.")

    parsed: Dict[str, str] = {}
    for line_num, raw_line in enumerate(raw_stdout.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = KEY_VALUE_PATTERN.match(line)
        if not match:
            raise CollectorError(
                f"Malformed KEY=VALUE line at line {line_num}: {raw_line!r}"
            )
        key, value = match.group(1), match.group(2).strip()
        parsed[key] = value

    missing_keys = REQUIRED_KEYS - parsed.keys()
    if missing_keys:
        sorted_missing = ", ".join(sorted(missing_keys))
        raise CollectorError(f"Missing required metric keys: {sorted_missing}")

    return parsed


def _parse_non_negative_int(data: Mapping[str, str], key: str) -> int:
    """Extract a key from data and convert it to a non-negative integer."""
    raw_val = data[key]
    try:
        val = int(raw_val)
    except ValueError as exc:
        raise CollectorError(
            f"Expected integer for {key}, got {raw_val!r}"
        ) from exc

    if val < 0:
        raise CollectorError(
            f"Expected non-negative integer for {key}, got {val}"
        )
    return val


def _build_cpu_sample(data: Mapping[str, str], prefix: str) -> CpuSample:
    """Construct a CpuSample dataclass from prefixed keys (CPU1 or CPU2)."""
    return CpuSample(
        user=_parse_non_negative_int(data, f"{prefix}_USER"),
        nice=_parse_non_negative_int(data, f"{prefix}_NICE"),
        system=_parse_non_negative_int(data, f"{prefix}_SYSTEM"),
        idle=_parse_non_negative_int(data, f"{prefix}_IDLE"),
        iowait=_parse_non_negative_int(data, f"{prefix}_IOWAIT"),
        irq=_parse_non_negative_int(data, f"{prefix}_IRQ"),
        softirq=_parse_non_negative_int(data, f"{prefix}_SOFTIRQ"),
        steal=_parse_non_negative_int(data, f"{prefix}_STEAL"),
    )


def parse_raw_metrics(raw_stdout: str) -> RawSystemMetrics:
    """Convert raw KEY=VALUE stdout string into a validated RawSystemMetrics instance."""
    data = parse_key_value_output(raw_stdout)

    network_status = data["NETWORK_STATUS"]
    if network_status not in VALID_NETWORK_STATUSES:
        raise CollectorError(
            f"Invalid NETWORK_STATUS value: {network_status!r}"
        )

    return RawSystemMetrics(
        timestamp_utc=data["TIMESTAMP_UTC"],
        cpu_sample_1=_build_cpu_sample(data, "CPU1"),
        cpu_sample_2=_build_cpu_sample(data, "CPU2"),
        mem_total_kb=_parse_non_negative_int(data, "MEM_TOTAL_KB"),
        mem_free_kb=_parse_non_negative_int(data, "MEM_FREE_KB"),
        mem_available_kb=_parse_non_negative_int(data, "MEM_AVAILABLE_KB"),
        mem_buffers_kb=_parse_non_negative_int(data, "MEM_BUFFERS_KB"),
        mem_cached_kb=_parse_non_negative_int(data, "MEM_CACHED_KB"),
        swap_total_kb=_parse_non_negative_int(data, "SWAP_TOTAL_KB"),
        swap_free_kb=_parse_non_negative_int(data, "SWAP_FREE_KB"),
        disk_mount=data["DISK_MOUNT"],
        disk_total_kb=_parse_non_negative_int(data, "DISK_TOTAL_KB"),
        disk_used_kb=_parse_non_negative_int(data, "DISK_USED_KB"),
        disk_available_kb=_parse_non_negative_int(data, "DISK_AVAILABLE_KB"),
        running_processes=_parse_non_negative_int(data, "RUNNING_PROCESSES"),
        net_rx_bytes=_parse_non_negative_int(data, "NET_RX_BYTES"),
        net_tx_bytes=_parse_non_negative_int(data, "NET_TX_BYTES"),
        network_status=network_status,
    )


def run_system_checks(
    bash_dir: Optional[Path] = None,
    env_overrides: Optional[Mapping[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Run bash/system_checks.sh --quiet and raise CollectorError if any check fails."""
    target_dir = bash_dir or get_default_bash_dir()
    script_path = target_dir / "system_checks.sh"
    _run_bash_script(
        script_path=script_path,
        args=["--quiet"],
        env_overrides=env_overrides,
        timeout=timeout,
    )


def collect_raw_metrics(
    bash_dir: Optional[Path] = None,
    env_overrides: Optional[Mapping[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RawSystemMetrics:
    """Execute bash/collect_metrics.sh and return a validated RawSystemMetrics object."""
    target_dir = bash_dir or get_default_bash_dir()
    script_path = target_dir / "collect_metrics.sh"
    stdout = _run_bash_script(
        script_path=script_path,
        args=["--quiet"],
        env_overrides=env_overrides,
        timeout=timeout,
    )
    return parse_raw_metrics(stdout)


if __name__ == "__main__":
    run_system_checks()
    metrics = collect_raw_metrics()
    print(f"Collected metrics at {metrics.timestamp_utc}:")
    print(f"  CPU Sample 1 Total Jiffies: {metrics.cpu_sample_1.total}")
    print(f"  CPU Sample 2 Total Jiffies: {metrics.cpu_sample_2.total}")
    print(f"  RAM Total / Available (KiB): {metrics.mem_total_kb} / {metrics.mem_available_kb}")
    print(f"  Disk ({metrics.disk_mount}) Used / Total (KiB): {metrics.disk_used_kb} / {metrics.disk_total_kb}")
    print(f"  Running Processes: {metrics.running_processes}")
    print(f"  Network Status: {metrics.network_status} (RX={metrics.net_rx_bytes} B, TX={metrics.net_tx_bytes} B)")