#!/usr/bin/env bash
# ==============================================================================
# Library Name: common.sh
# Description: Shared constants, structured logging, signal trapping, and
#              defensive file verification helpers for Bash monitoring scripts.
# ==============================================================================

# Prevent multiple sourcing of this library within the same shell process
if [[ -n "${_LHM_COMMON_SH_LOADED:-}" ]]; then
    return 0
fi
readonly _LHM_COMMON_SH_LOADED=1

# Resolve paths (allowing environment overrides for isolated unit testing)
readonly LHM_PROC_DIR="${LHM_PROC_DIR:-/proc}"
readonly LHM_PROJECT_ROOT="${LHM_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
readonly LHM_LOG_FILE="${LHM_LOG_FILE:-${LHM_PROJECT_ROOT}/logs/monitor.log}"

# Standard exit codes shared across all Bash scripts
readonly EXIT_SUCCESS=0
readonly EXIT_INVALID_ARGS=1
readonly EXIT_PROC_ERROR=2
readonly EXIT_MISSING_CMD=3
readonly EXIT_DIR_ERROR=4

# Global state for quiet output and tracked temporary files
LHM_QUIET_MODE="${LHM_QUIET_MODE:-false}"
declare -a LHM_TEMP_FILES=()

# Generate an ISO-8601 UTC timestamp
get_utc_timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# Internal helper: append a formatted line to LHM_LOG_FILE if its parent dir is writable
_write_to_log_file() {
    local level="$1"
    local message="$2"
    local log_dir
    log_dir="$(dirname "${LHM_LOG_FILE}")"

    if [[ -d "${log_dir}" && -w "${log_dir}" ]]; then
        printf "%s [%s] %s\n" "$(get_utc_timestamp)" "${level}" "${message}" >> "${LHM_LOG_FILE}" 2>/dev/null || true
    fi
}

log_info() {
    local message="$1"
    _write_to_log_file "INFO" "${message}"
    if [[ "${LHM_QUIET_MODE}" == false ]]; then
        printf "[INFO] %s\n" "${message}"
    fi
}

log_warn() {
    local message="$1"
    _write_to_log_file "WARN" "${message}"
    if [[ "${LHM_QUIET_MODE}" == false ]]; then
        printf "[WARN] %s\n" "${message}" >&2
    fi
}

log_error() {
    local message="$1"
    _write_to_log_file "ERROR" "${message}"
    printf "[ERROR] %s\n" "${message}" >&2
}

# Register a temporary file so the cleanup trap removes it automatically on exit
register_temp_file() {
    local temp_path="$1"
    LHM_TEMP_FILES+=("${temp_path}")
}

# Cleanup handler invoked automatically on EXIT, SIGINT, or SIGTERM
cleanup_on_exit() {
    local exit_code=$?
    local temp_file
    for temp_file in "${LHM_TEMP_FILES[@]:-}"; do
        if [[ -n "${temp_file}" && -e "${temp_file}" ]]; then
            rm -f "${temp_file}"
        fi
    done
    return "${exit_code}"
}

# Install traps for normal exit, interrupt (Ctrl+C), and termination signals
setup_cleanup_trap() {
    trap cleanup_on_exit EXIT INT TERM
}