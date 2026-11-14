import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.analyzer import (  # noqa: E402
    AnalyzerError,
    analyze_metrics,
    evaluate_thresholds,
)
from python.collector import (  # noqa: E402
    CollectorError,
    collect_raw_metrics,
    run_system_checks,
)
from python.config import ConfigError, load_threshold_config  # noqa: E402
from python.database import (  # noqa: E402
    DatabaseError,
    fetch_recent_history,
    fetch_summary_stats,
    save_evaluation,
)
from python.reporter import (  # noqa: E402
    configure_logger,
    format_health_report,
    format_history_report,
    log_evaluation,
)

EXIT_OK = 0
EXIT_COLLECTOR_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_ANALYZER_ERROR = 4
EXIT_DATABASE_ERROR = 5


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="linux-health-monitor",
        description="Collect, analyze, store, and report Linux system health metrics.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to thresholds JSON configuration file (default: config/thresholds.json).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database file (default: data/metrics.db).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to application log file (default: logs/app.log).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save the collected snapshot to the SQLite database.",
    )
    parser.add_argument(
        "--history",
        type=int,
        metavar="N",
        default=None,
        help="Display historical summary and the last N recorded snapshots from SQLite.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress stdout report output (useful for automated cron execution).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the CLI workflow and return an integer exit code."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logger = configure_logger(args.log_file)

    try:
        if args.history is not None:
            records = fetch_recent_history(limit=args.history, db_path=args.db)
            summary = fetch_summary_stats(db_path=args.db)
            if not args.quiet:
                print(format_history_report(records, summary))
            return EXIT_OK

        run_system_checks()
        thresholds = load_threshold_config(args.config)
        raw_metrics = collect_raw_metrics()
        metrics = analyze_metrics(raw_metrics)
        evaluation = evaluate_thresholds(metrics, thresholds)

        log_evaluation(logger, evaluation)

        if not args.no_save:
            save_evaluation(evaluation, db_path=args.db)

        if not args.quiet:
            print(format_health_report(evaluation))

        return EXIT_OK

    except CollectorError as exc:
        logger.error("Collector failure: %s", exc)
        print(f"[ERROR] Collector failure: {exc}", file=sys.stderr)
        return EXIT_COLLECTOR_ERROR
    except ConfigError as exc:
        logger.error("Configuration failure: %s", exc)
        print(f"[ERROR] Configuration failure: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except AnalyzerError as exc:
        logger.error("Analyzer failure: %s", exc)
        print(f"[ERROR] Analyzer failure: {exc}", file=sys.stderr)
        return EXIT_ANALYZER_ERROR
    except DatabaseError as exc:
        logger.error("Database failure: %s", exc)
        print(f"[ERROR] Database failure: {exc}", file=sys.stderr)
        return EXIT_DATABASE_ERROR


if __name__ == "__main__":
    sys.exit(main())
