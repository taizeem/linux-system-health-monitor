#!/usr/bin/env bash
# ==============================================================================
# Script Name: setup_cron.sh
# Description: Idempotent helper to print, install, check status, or remove the
#              Linux System Health Monitor cron schedule.
# Usage:
#   ./bash/setup_cron.sh --print [SCHEDULE]
#   ./bash/setup_cron.sh --install [SCHEDULE]
#   ./bash/setup_cron.sh --status
#   ./bash/setup_cron.sh --remove
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bash/common.sh
source "${SCRIPT_DIR}/common.sh"

readonly CRONTAB_BIN="${LHM_CRONTAB_BIN:-crontab}"
readonly CRON_TAG="# LHM_MANAGED_CRON_JOB"
readonly DEFAULT_SCHEDULE="*/5 * * * *"
readonly RUNNER_PATH="${LHM_PROJECT_ROOT}/bash/cron_runner.sh"

usage() {
    cat <<USAGE_EOF
Usage: $(basename "$0") [OPTION] [CRON_SCHEDULE]

Options:
  --print [SCHEDULE]    Print the crontab line without modifying crontab
  --install [SCHEDULE]  Install or update the cron job (default: "${DEFAULT_SCHEDULE}")
  --status              Check whether the cron job is currently installed
  --remove              Remove the managed cron job from user crontab
  --help, -h            Display this help message
USAGE_EOF
}

validate_cron_schedule() {
    local sched="$1"
    # Verify schedule has exactly 5 whitespace-separated fields
    local field_count
    field_count="$(awk '{print NF}' <<< "${sched}")"
    if [[ "${field_count}" -ne 5 ]]; then
        log_error "Invalid cron schedule (expected 5 fields): '${sched}'"
        exit "${EXIT_INVALID_ARGS}"
    fi
}

build_cron_line() {
    local sched="$1"
    validate_cron_schedule "${sched}"
    printf "%s \"%s\" %s\n" "${sched}" "${RUNNER_PATH}" "${CRON_TAG}"
}

get_existing_crontab() {
    "${CRONTAB_BIN}" -l 2>/dev/null || true
}

install_cron_job() {
    local sched="$1"
    local cron_line
    cron_line="$(build_cron_line "${sched}")"

    local current_crontab
    current_crontab="$(get_existing_crontab)"

    local filtered_crontab
    filtered_crontab="$(grep -v "${CRON_TAG}" <<< "${current_crontab}" || true)"

    local updated_crontab
    if [[ -n "${filtered_crontab}" ]]; then
        updated_crontab="$(printf "%s\n%s\n" "${filtered_crontab}" "${cron_line}")"
    else
        updated_crontab="$(printf "%s\n" "${cron_line}")"
    fi

    printf "%s\n" "${updated_crontab}" | "${CRONTAB_BIN}" -
    log_info "Installed cron schedule: ${cron_line}"
}

remove_cron_job() {
    local current_crontab
    current_crontab="$(get_existing_crontab)"

    local filtered_crontab
    filtered_crontab="$(grep -v "${CRON_TAG}" <<< "${current_crontab}" || true)"

    printf "%s\n" "${filtered_crontab}" | "${CRONTAB_BIN}" -
    log_info "Removed managed cron job (${CRON_TAG})."
}

show_status() {
    local current_crontab
    current_crontab="$(get_existing_crontab)"
    if grep -q "${CRON_TAG}" <<< "${current_crontab}"; then
        log_info "Active cron job found:"
        grep "${CRON_TAG}" <<< "${current_crontab}"
        exit "${EXIT_SUCCESS}"
    else
        log_info "No active cron job found for ${CRON_TAG}."
        exit "${EXIT_SUCCESS}"
    fi
}

main() {
    if [[ $# -eq 0 ]]; then
        usage >&2
        exit "${EXIT_INVALID_ARGS}"
    fi

    local action="$1"
    local schedule="${2:-${DEFAULT_SCHEDULE}}"

    case "${action}" in
        --print)
            build_cron_line "${schedule}"
            ;;
        --install)
            install_cron_job "${schedule}"
            ;;
        --remove)
            remove_cron_job
            ;;
        --status)
            show_status
            ;;
        --help|-h)
            usage
            exit "${EXIT_SUCCESS}"
            ;;
        *)
            log_error "Unknown option: ${action}"
            usage >&2
            exit "${EXIT_INVALID_ARGS}"
            ;;
    esac
}

main "$@"
