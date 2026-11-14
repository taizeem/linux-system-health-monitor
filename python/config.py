from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ConfigError(ValueError):
    """Raised when the configuration file is missing, unreadable, or invalid."""


def _validate_percentage_field(name: str, value: Any) -> float:
    """Validate that a field is a number (not bool) in the range (0.0, 100.0]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"Threshold '{name}' must be a number, got {value!r}")
    float_val = float(value)
    if float_val <= 0.0 or float_val > 100.0:
        raise ConfigError(
            f"Threshold '{name}' must be between 0.0 (exclusive) and 100.0 (inclusive), got {float_val}"
        )
    return float_val


def _validate_positive_int_field(name: str, value: Any) -> int:
    """Validate that a field is a positive integer (not bool or float)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"Threshold '{name}' must be an integer, got {value!r}")
    if value <= 0:
        raise ConfigError(f"Threshold '{name}' must be greater than 0, got {value}")
    return value


@dataclass(frozen=True)
class ThresholdConfig:
    """Immutable resource usage threshold settings."""

    cpu_percent: float = 80.0
    ram_percent: float = 70.0
    swap_percent: float = 50.0
    disk_percent: float = 85.0
    max_processes: int = 500
    require_network: bool = True

    def __post_init__(self) -> None:
        """Validate all threshold values upon initialization."""
        object.__setattr__(
            self, "cpu_percent", _validate_percentage_field("cpu_percent", self.cpu_percent)
        )
        object.__setattr__(
            self, "ram_percent", _validate_percentage_field("ram_percent", self.ram_percent)
        )
        object.__setattr__(
            self, "swap_percent", _validate_percentage_field("swap_percent", self.swap_percent)
        )
        object.__setattr__(
            self, "disk_percent", _validate_percentage_field("disk_percent", self.disk_percent)
        )
        object.__setattr__(
            self,
            "max_processes",
            _validate_positive_int_field("max_processes", self.max_processes),
        )
        if not isinstance(self.require_network, bool):
            raise ConfigError(
                f"Threshold 'require_network' must be a boolean, got {self.require_network!r}"
            )


def get_default_config_path() -> Path:
    """Return the config path from LHM_CONFIG_FILE env var or config/thresholds.json."""
    env_path = os.environ.get("LHM_CONFIG_FILE")
    if env_path:
        return Path(env_path)
    return PROJECT_ROOT / "config" / "thresholds.json"


def load_threshold_config(config_path: Optional[Path] = None) -> ThresholdConfig:
    """Load, parse, and validate ThresholdConfig from a JSON file."""
    target_path = config_path or get_default_config_path()

    try:
        raw_text = target_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {target_path}") from exc
    except PermissionError as exc:
        raise ConfigError(f"Permission denied reading configuration file: {target_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Error reading configuration file {target_path}: {exc}") from exc

    try:
        parsed: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Malformed JSON in {target_path}: {exc.msg}") from exc

    if not isinstance(parsed, Mapping):
        raise ConfigError(f"Expected JSON object in {target_path}, got {type(parsed).__name__}")

    allowed_keys = {
        "cpu_percent",
        "ram_percent",
        "swap_percent",
        "disk_percent",
        "max_processes",
        "require_network",
    }
    unknown_keys = set(parsed.keys()) - allowed_keys
    if unknown_keys:
        sorted_unknown = ", ".join(sorted(unknown_keys))
        raise ConfigError(f"Unknown configuration keys in {target_path}: {sorted_unknown}")

    return ThresholdConfig(
        cpu_percent=parsed.get("cpu_percent", 80.0),
        ram_percent=parsed.get("ram_percent", 70.0),
        swap_percent=parsed.get("swap_percent", 50.0),
        disk_percent=parsed.get("disk_percent", 85.0),
        max_processes=parsed.get("max_processes", 500),
        require_network=parsed.get("require_network", True),
    )


if __name__ == "__main__":
    cfg = load_threshold_config()
    print(f"Loaded ThresholdConfig: {cfg}")
