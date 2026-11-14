#!/usr/bin/env bash
# ==============================================================================
# Script Name: cron_runner.sh
# Description: Unattended execution wrapper for cron. Sanitizes PATH, acquires
#              a non-blocking flock lockfile to prevent overlapping runs, and
#              invokes python/main.py --quiet.
# Exit Codes:
#   0 - Monitoring snapshot completed successfully
#   6 - Another monitoring instance is currently holding the lock
#   * - Propagates any non-zero exit code from python/main.py
# ==============================================================================

set -euo pipefail

# Ensure standard system binaries are in PATH when invoked by cron(8)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bash/common.sh
source "${SCRIPT_DIR}/common.sh"

setup_cleanup_trap

readonly LOCK_FILE="${LHM_LOCK_FILE:-${LHM_PROJECT_ROOT}/data/cron_monitor.lock}"
readonly CRON_LOG_FILE="${LHM_CRON_LOG_FILE:-${LHM_PROJECT_ROOT}/logs/cron.log}"
readonly PYTHON_BIN="${LHM_PYTHON_BIN:-python3}"
readonly MAIN_SCRIPT="${LHM_MAIN_SCRIPT:-${LHM_PROJECT_ROOT}/python/main.py}"

main() {
    mkdir -p "$(dirname "${LOCK_FILE}")" "$(dirname "${CRON_LOG_FILE}")"

    # Open lockfile on file descriptor 9 and attempt non-blocking exclusive lock
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        log_warn "Skipping cron run: another instance holds lock ${LOCK_FILE}"
        exit "${EXIT_LOCK_ERROR}"
    fi

    _write_to_log_file "INFO" "Cron runner starting scheduled health check."
    if ! "${PYTHON_BIN}" "${MAIN_SCRIPT}" --quiet >> "${CRON_LOG_FILE}" 2>&1; then
        local rc=$?
        log_error "Scheduled health check failed with exit code ${rc}"
        exit "${rc}"
    fi
    _write_to_log_file "INFO" "Cron runner completed scheduled health check."
    exit "${EXIT_SUCCESS}"
}

main "$@"
