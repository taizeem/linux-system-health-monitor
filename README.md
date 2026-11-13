# Linux System Health Monitor

A modular Linux system health monitoring CLI built with Bash and Python (standard library), reading directly from the Linux `/proc` virtual filesystem and storing historical metrics in SQLite.

## Project Structure

- `bash/`: System-level environment checks and raw metric collection scripts.
- `python/`: Metric parsing, threshold analysis, SQLite storage, and CLI reporting.
- `tests/`: Automated unit and integration test suites (`unittest`).
- `config/`: Threshold and monitoring configuration files.
- `data/`: Local SQLite database storage (ignored by Git).
- `logs/`: Application and cron execution logs (ignored by Git).

## Quick Start (Phase 1)

1. Verify system compatibility:
   ```bash
   ./bash/system_checks.sh