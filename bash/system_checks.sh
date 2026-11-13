#!/usr/bin/env bash
# ==============================================================================
# Script Name: system_checks.sh
# Description: Validates that the host environment is Linux, exposes required
#              /proc virtual files, has required core utilities installed, and
#              has writable runtime directories.
# Exit Codes:
#   0 - All system checks passed
#   1 - Invalid command-line argument or usage error
#   2 - Missing or unreadable /proc virtual file
#   3 - Missing required system binary/command
#   4 - Missing or non-writable project runtime directory
# ==============================================================================

set -euo pipefail

# Allow overriding paths via environment variables so automated tests can
# inject mock directories without modifying production code.
readonly PROC_DIR="${LHM_PROC_DIR:-/proc}"
readonly PROJECT_ROOT="${LHM_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Exit code constants
readonly EXIT_SUCCESS=0
readonly EXIT_INVALID_ARGS=1
readonly EXIT_PROC_ERROR=2
readonly EXIT_MISSING_CMD=3
readonly EXIT_DIR_ERROR=4

log_info() {
    printf "[INFO] %s\n" "$1"
}

log_error() {
    printf "[ERROR] %s\n" "$1" >&2
}

usage() {
    printf "Usage: %s [--quiet]\n" "$(basename "$0")"
}

# Parse command-line arguments
QUIET_MODE=false
if [[ $# -gt 0 ]]; then
    case "$1" in
        --quiet|-q)
            QUIET_MODE=true
            ;;
        --help|-h)
            usage
            exit "${EXIT_SUCCESS}"
            ;;
        *)
            log_error "Unknown argument: $1"
            usage >&2
            exit "${EXIT_INVALID_ARGS}"
            ;;
    esac
fi

info_msg() {
    if [[ "${QUIET_MODE}" == false ]]; then
        log_info "$1"
    fi
}

check_proc_files() {
    local required_files=(
        "stat"
        "meminfo"
        "uptime"
        "loadavg"
        "net/dev"
    )

    info_msg "Checking Linux /proc filesystem interfaces in ${PROC_DIR}..."

    if [[ ! -d "${PROC_DIR}" ]]; then
        log_error "Proc directory does not exist: ${PROC_DIR}"
        exit "${EXIT_PROC_ERROR}"
    fi

    local file_rel_path
    local full_path
    for file_rel_path in "${required_files[@]}"; do
        full_path="${PROC_DIR}/${file_rel_path}"
        if [[ ! -e "${full_path}" ]]; then
            log_error "Required kernel interface file is missing: ${full_path}"
            exit "${EXIT_PROC_ERROR}"
        fi
        if [[ ! -r "${full_path}" ]]; then
            log_error "Required kernel interface file is not readable: ${full_path}"
            exit "${EXIT_PROC_ERROR}"
        fi
    done

    info_msg "All required /proc virtual files are present and readable."
}

check_required_commands() {
    # Allow overriding the required commands list in tests via LHM_REQUIRED_CMDS
    local default_cmds=("df" "ps" "ping" "awk" "grep" "python3")
    local cmds_to_check=()

    if [[ -n "${LHM_REQUIRED_CMDS:-}" ]]; then
        read -r -a cmds_to_check <<< "${LHM_REQUIRED_CMDS}"
    else
        cmds_to_check=("${default_cmds[@]}")
    fi

    info_msg "Checking required system utilities..."

    local cmd
    for cmd in "${cmds_to_check[@]}"; do
        if ! command -v "${cmd}" >/dev/null 2>&1; then
            log_error "Required command is not installed or not in PATH: ${cmd}"
            exit "${EXIT_MISSING_CMD}"
        fi
    done

    info_msg "All required system utilities are available."
}

check_runtime_directories() {
    local required_dirs=(
        "logs"
        "data"
        "config"
    )

    info_msg "Checking project runtime directories in ${PROJECT_ROOT}..."

    local dir_name
    local full_dir_path
    for dir_name in "${required_dirs[@]}"; do
        full_dir_path="${PROJECT_ROOT}/${dir_name}"
        if [[ ! -d "${full_dir_path}" ]]; then
            log_error "Required directory is missing: ${full_dir_path}"
            exit "${EXIT_DIR_ERROR}"
        fi
        if [[ ! -w "${full_dir_path}" ]]; then
            log_error "Required directory is not writable: ${full_dir_path}"
            exit "${EXIT_DIR_ERROR}"
        fi
    done

    info_msg "All runtime directories exist and are writable."
}

main() {
    check_proc_files
    check_required_commands
    check_runtime_directories
    info_msg "System environment verification passed."
    exit "${EXIT_SUCCESS}"
}

main
