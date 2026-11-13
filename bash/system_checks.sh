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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bash/common.sh
source "${SCRIPT_DIR}/common.sh"

setup_cleanup_trap

usage() {
    printf "Usage: %s [--quiet]\n" "$(basename "$0")"
}

parse_args() {
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --quiet|-q)
                LHM_QUIET_MODE=true
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
}

check_proc_files() {
    local required_files=(
        "stat"
        "meminfo"
        "uptime"
        "loadavg"
        "net/dev"
    )

    log_info "Checking Linux /proc filesystem interfaces in ${LHM_PROC_DIR}..."

    if [[ ! -d "${LHM_PROC_DIR}" ]]; then
        log_error "Proc directory does not exist: ${LHM_PROC_DIR}"
        exit "${EXIT_PROC_ERROR}"
    fi

    local file_rel_path
    local full_path
    for file_rel_path in "${required_files[@]}"; do
        full_path="${LHM_PROC_DIR}/${file_rel_path}"
        if [[ ! -e "${full_path}" ]]; then
            log_error "Required kernel interface file is missing: ${full_path}"
            exit "${EXIT_PROC_ERROR}"
        fi
        if [[ ! -r "${full_path}" ]]; then
            log_error "Required kernel interface file is not readable: ${full_path}"
            exit "${EXIT_PROC_ERROR}"
        fi
    done

    log_info "All required /proc virtual files are present and readable."
}

check_required_commands() {
    local default_cmds=("df" "ps" "ping" "awk" "grep" "python3")
    local cmds_to_check=()

    if [[ -n "${LHM_REQUIRED_CMDS:-}" ]]; then
        read -r -a cmds_to_check <<< "${LHM_REQUIRED_CMDS}"
    else
        cmds_to_check=("${default_cmds[@]}")
    fi

    log_info "Checking required system utilities..."

    local cmd
    for cmd in "${cmds_to_check[@]}"; do
        if ! command -v "${cmd}" >/dev/null 2>&1; then
            log_error "Required command is not installed or not in PATH: ${cmd}"
            exit "${EXIT_MISSING_CMD}"
        fi
    done

    log_info "All required system utilities are available."
}

check_runtime_directories() {
    local required_dirs=(
        "logs"
        "data"
        "config"
    )

    log_info "Checking project runtime directories in ${LHM_PROJECT_ROOT}..."

    local dir_name
    local full_dir_path
    for dir_name in "${required_dirs[@]}"; do
        full_dir_path="${LHM_PROJECT_ROOT}/${dir_name}"
        if [[ ! -d "${full_dir_path}" ]]; then
            log_error "Required directory is missing: ${full_dir_path}"
            exit "${EXIT_DIR_ERROR}"
        fi
        if [[ ! -w "${full_dir_path}" ]]; then
            log_error "Required directory is not writable: ${full_dir_path}"
            exit "${EXIT_DIR_ERROR}"
        fi
    done

    # Verify temporary file creation and automatic cleanup via trap
    local probe_tmp="${LHM_PROJECT_ROOT}/data/.check_write_probe.$$"
    register_temp_file "${probe_tmp}"
    if ! printf "ok\n" > "${probe_tmp}" 2>/dev/null; then
        log_error "Failed to write probe file inside data directory: ${probe_tmp}"
        exit "${EXIT_DIR_ERROR}"
    fi

    log_info "All runtime directories exist and are writable."
}

main() {
    parse_args "$@"
    check_proc_files
    check_required_commands
    check_runtime_directories
    log_info "System environment verification passed."
    exit "${EXIT_SUCCESS}"
}

main "$@"