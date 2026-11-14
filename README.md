# Linux System Health Monitor

A production-grade command-line interface (CLI) and automated background telemetry service for Linux systems, built using defensive [GNU Bash](https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html) scripting and the [Python 3 Standard Library](https://docs.python.org/3/library/index.html) with zero third-party runtime dependencies.

## Overview

The **Linux System Health Monitor** separates low-level operating system probing from application-level calculation, threshold detection, persistence, and reporting:

- **Kernel `/proc` Telemetry**: Reads two-point CPU jiffy snapshots from `/proc/stat`, physical memory and swap capacity from `/proc/meminfo`, and non-loopback network interface traffic counters from `/proc/net/dev` via the [Linux `/proc` Virtual Filesystem](https://docs.kernel.org/filesystems/proc.html) ([`proc(5)`](https://man7.org/linux/man-pages/man5/proc.5.html)) and [GNU Awk](https://www.gnu.org/software/gawk/manual/gawk.html).
- **Filesystem, Process, and Reachability Probes**: Queries mounted filesystem capacity via POSIX `df -P -k` ([`statvfs(3)`](https://man7.org/linux/man-pages/man3/statvfs.3.html)), counts active tasks via [`ps(1)`](https://man7.org/linux/man-pages/man1/ps.1.html), and verifies external network connectivity via ICMP Echo requests ([`ping(8)`](https://man7.org/linux/man-pages/man8/ping.8.html)).
- **Type-Safe Subprocess Bridge**: Executes Bash collectors safely from Python via [`subprocess`](https://docs.python.org/3/library/subprocess.html) (`shell=False` with execution timeouts), validates output lines with compiled [`re`](https://docs.python.org/3/library/re.html) regular expressions, and hydrates immutable [`dataclasses`](https://docs.python.org/3/library/dataclasses.html).
- **Configurable Threshold Detection**: Loads alert limits from `config/thresholds.json` via [`json`](https://docs.python.org/3/library/json.html) and flags CPU, RAM, Swap, Disk, Process, and Network violations.
- **Concurrent SQLite WAL Storage**: Persists historical snapshots in `data/metrics.db` using [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) configured with [SQLite Write-Ahead Logging (WAL)](https://www.sqlite.org/wal.html) and `0600` (`rw-------`) owner-only file permissions.
- **Bounded Rotating Logs & Signal Traps**: Cleans up temporary files automatically on `EXIT`, `SIGINT`, and `SIGTERM` in Bash, and rotates application logs at `5 MiB` (up to 3 backups) via [`logging.handlers.RotatingFileHandler`](https://docs.python.org/3/library/logging.handlers.html).
- **Overlap-Protected Cron Automation**: Prevents overlapping scheduled runs using non-blocking kernel file locks ([`flock(1)`](https://man7.org/linux/man-pages/man1/flock.1.html)) and manages user [`crontab(5)`](https://man7.org/linux/man-pages/man5/crontab.5.html) schedules for the [`cron(8)`](https://www.man7.org/linux/man-pages/man8/cron.8.html) daemon.

## Project Structure

```text
linux-health-monitor/
├── ARCHITECTURE.md                  # Internal kernel-to-CLI architecture documentation
├── README.md                        # Setup, usage, configuration, and exit code guide
├── requirements.txt                 # Standard library dependency manifest
├── .gitignore                       # Excludes logs, SQLite DB/WAL files, lockfiles, caches
├── bash/
│   ├── common.sh                    # Shared UTC logging, exit codes, and signal traps
│   ├── system_checks.sh             # Pre-flight /proc, binary, and directory verifier
│   ├── collect_metrics.sh           # Raw metric collector emitting KEY=VALUE stdout
│   ├── cron_runner.sh               # Non-blocking flock(1) wrapper for cron jobs
│   └── setup_cron.sh                # Idempotent crontab installer, status, and remover
├── config/
│   └── thresholds.json              # Externalized resource usage alert thresholds
├── data/                            # SQLite WAL database & lockfiles (git-ignored)
├── logs/                            # Rotating application & cron logs (git-ignored)
├── python/
│   ├── __init__.py
│   ├── main.py                      # Main CLI entrypoint (argparse)
│   ├── collector.py                 # Subprocess runner, regex parser, raw dataclasses
│   ├── analyzer.py                  # Utilization math and threshold violation engine
│   ├── config.py                    # Threshold JSON loader and schema validator
│   ├── database.py                  # SQLite WAL persistence and summary aggregations
│   └── reporter.py                  # CLI report formatter and RotatingFileHandler setup
└── tests/
    ├── test_system_checks.py        # Environment verification & signal trap tests
    ├── test_collect_metrics.py      # Bash metric collection & corrupted /proc tests
    ├── test_collector.py            # Subprocess bridge, timeout, and parser tests
    ├── test_analyzer.py             # CPU/RAM/Swap/Disk math & edge-case tests
    ├── test_config_and_thresholds.py# Config validation & boundary value tests
    ├── test_database.py             # SQLite CRUD, WAL, and permission error tests
    ├── test_reporter_and_main.py    # Report formatting, logging, and CLI tests
    ├── test_cron.py                 # flock contention & idempotent crontab tests
    └── test_security_and_project.py # File mode, AST shell=False, and deliverable audits
```

## Requirements

- **Operating System**: Linux (Ubuntu, Debian, Fedora, RHEL, Arch, or any distribution exposing `/proc`)
- **Shell**: GNU Bash `4.3+`
- **Core Utilities**: `awk`, `grep`, `df`, `ps`, `ping`, `flock`, `crontab`
- **Python**: Python `3.9+` (uses only standard library modules; follows [PEP 8](https://peps.python.org/pep-0008/))

## Quick Start

### 1. Clone the Repository and Verify System Readiness

```bash
git clone https://github.com/taizeem/linux-system-health-monitor.git
cd linux-system-health-monitor
chmod +x bash/*.sh
./bash/system_checks.sh
```

### 2. Run a Live System Health Check

```bash
python3 python/main.py
```

Example output:

```text
========== SYSTEM HEALTH ==========

CPU Usage:          45%
RAM Usage:          72%
Swap Usage:         12%
Disk Usage:         68%
Running Processes:  124
Network:            Connected

[WARNING] RAM usage is above 70%
[STATUS] System is operational

===================================
```

### 3. View Historical Metrics and Summary Statistics

Query the SQLite database for aggregate metrics (`AVG`, `MAX`, warning counts) and the most recent `N` snapshots:

```bash
python3 python/main.py --history 10
```

Example output:

```text
======= HISTORICAL SUMMARY ========

Total Snapshots:    12
Avg / Max CPU:      18.4% / 45%
Avg / Max RAM:      54.2% / 72%
Max Disk Usage:     68%
Warning Snapshots:  1

Recent Snapshots (Newest First):
  [2026-11-14T06:45:00Z] CPU=45% RAM=72% Swap=12% Disk=68% Procs=124 Net=Connected (WARN)
  [2026-11-14T06:40:00Z] CPU=14.2% RAM=52.1% Swap=12% Disk=68% Procs=121 Net=Connected (OK)

===================================
```

## Configuration

Resource thresholds are configured in `config/thresholds.json`:

```json
{
  "cpu_percent": 80.0,
  "ram_percent": 70.0,
  "swap_percent": 50.0,
  "disk_percent": 85.0,
  "max_processes": 500,
  "require_network": true
}
```

| Key | Type | Valid Range | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `cpu_percent` | `float` | `(0.0, 100.0]` | `80.0` | Warn when aggregate CPU usage exceeds this percentage |
| `ram_percent` | `float` | `(0.0, 100.0]` | `70.0` | Warn when `(MemTotal - MemAvailable) / MemTotal` exceeds this percentage |
| `swap_percent` | `float` | `(0.0, 100.0]` | `50.0` | Warn when swap usage exceeds this percentage (`0.0%` if swap is disabled) |
| `disk_percent` | `float` | `(0.0, 100.0]` | `85.0` | Warn when root/target filesystem usage exceeds this percentage |
| `max_processes` | `int` | `> 0` | `500` | Warn when total running processes exceed this count |
| `require_network` | `bool` | `true` / `false` | `true` | Warn when external ICMP reachability reports `Disconnected` |

## CLI Options and Environment Overrides

### Command-Line Arguments ([`argparse`](https://docs.python.org/3/library/argparse.html))

```bash
python3 python/main.py --help
```

| Flag | Description |
| :--- | :--- |
| `--config PATH` | Use a custom JSON threshold configuration file |
| `--db PATH` | Use a custom SQLite database file path (default: `data/metrics.db`) |
| `--log-file PATH` | Use a custom rotating application log path (default: `logs/app.log`) |
| `--no-save` | Run live collection and print the health report without writing to SQLite |
| `--history N` | Print aggregate statistics and the `N` most recent snapshots from SQLite |
| `--quiet`, `-q` | Suppress `stdout` report output (used by background `cron` jobs) |

### Environment Variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `LHM_PROC_DIR` | `/proc` | Path to the kernel procfs mount |
| `LHM_DISK_MOUNT` | `/` | Filesystem mount point monitored by `df -P -k` |
| `LHM_PING_TARGET` | `8.8.8.8` | Target host for ICMP connectivity checks |
| `LHM_PING_TIMEOUT` | `2` | Timeout in seconds for `ping` |
| `LHM_CPU_SAMPLE_INTERVAL` | `0.2` | Delay in seconds between the two `/proc/stat` snapshots |
| `LHM_CONFIG_FILE` | `config/thresholds.json` | Default threshold configuration path |
| `LHM_DB_FILE` | `data/metrics.db` | Default SQLite database path |
| `LHM_APP_LOG_FILE` | `logs/app.log` | Default rotating Python log file path |

## Automated Monitoring with Cron

Use `bash/setup_cron.sh` to manage your user crontab idempotently:

```bash
# 1. Preview the generated crontab entry
./bash/setup_cron.sh --print "*/5 * * * *"

# 2. Install or update the scheduled job (runs every 5 minutes by default)
./bash/setup_cron.sh --install "*/5 * * * *"

# 3. Verify that the managed cron job is active
./bash/setup_cron.sh --status

# 4. Remove the managed cron job without affecting other crontab entries
./bash/setup_cron.sh --remove
```

## Exit Codes

| Layer | Exit Code | Constant | Condition |
| :--- | :--- | :--- | :--- |
| Bash & Python | `0` | `EXIT_SUCCESS` / `EXIT_OK` | Execution completed without errors |
| Bash | `1` | `EXIT_INVALID_ARGS` | Unknown CLI flag or invalid cron schedule syntax |
| Bash & Python | `2` | `EXIT_PROC_ERROR` / `EXIT_COLLECTOR_ERROR` | Missing/unreadable `/proc` file or collector subprocess failure |
| Bash & Python | `3` | `EXIT_MISSING_CMD` / `EXIT_CONFIG_ERROR` | Missing required OS binary (Bash) or invalid `thresholds.json` (Python) |
| Bash & Python | `4` | `EXIT_DIR_ERROR` / `EXIT_ANALYZER_ERROR` | Missing/read-only runtime directory (Bash) or invalid metric capacity (Python) |
| Bash & Python | `5` | `EXIT_PARSE_ERROR` / `EXIT_DATABASE_ERROR` | Corrupted `/proc` output (Bash) or SQLite database error (Python) |
| Bash | `6` | `EXIT_LOCK_ERROR` | Cron run skipped because an earlier instance holds `data/cron_monitor.lock` |

## Testing

The test suite uses Python's built-in [`unittest`](https://docs.python.org/3/library/unittest.html) framework across 9 test modules (94 test cases) covering normal operation, boundary conditions, missing/unreadable files, corrupted `/proc` streams, timeouts, SQLite failures, `flock` lock contention, and AST security checks:

```bash
python3 -m unittest discover -s tests -v
```
