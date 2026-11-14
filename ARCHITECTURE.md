# Architecture Guide: Linux System Health Monitor

## 1. High-Level Design Philosophy

The Linux System Health Monitor separates **OS-level probing** (handled by defensive Bash scripts) from **data parsing, mathematical modeling, threshold evaluation, persistence, and reporting** (handled by Python 3's standard library).

```text
+-------------------------------------------------------------------------+
|                        Linux Kernel Space                               |
|   /proc/stat     /proc/meminfo     /proc/net/dev     VFS / statvfs()    |
+-------------------------------------------------------------------------+
                                    |
                                    | (System Calls: open, read, statfs, pipe)
                                    v
+-------------------------------------------------------------------------+
|                    Bash OS Collection Layer (bash/)                     |
|  - common.sh          : UTC logging, EXIT/INT/TERM signal traps         |
|  - system_checks.sh   : Verifies /proc interfaces, binaries, and dirs   |
|  - collect_metrics.sh : Samples CPU jiffies (t1, t2), RAM, Swap, Disk,  |
|                         Processes, and Network into KEY=VALUE stdout    |
|  - cron_runner.sh     : Non-blocking flock(1) concurrency wrapper       |
+-------------------------------------------------------------------------+
                                    |
                                    | (subprocess.run pipe, shell=False)
                                    v
+-------------------------------------------------------------------------+
|                   Python Processing Layer (python/)                     |
|  - collector.py : Regex validation -> frozen RawSystemMetrics dataclass |
|  - analyzer.py  : Delta/ratio math -> SystemMetrics & HealthEvaluation  |
|  - config.py    : Loads & validates config/thresholds.json              |
|  - database.py  : SQLite3 WAL persistence (0600 mode) & SQL aggregates  |
|  - reporter.py  : CLI report formatting & 5 MiB RotatingFileHandler     |
|  - main.py      : CLI entrypoint (--config, --db, --history, --quiet)   |
+-------------------------------------------------------------------------+
