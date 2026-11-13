#!/usr/bin/env bash
# ==============================================================================
# Script Name: collect_metrics.sh
# Description: Collects raw system metrics from /proc/stat and /proc/meminfo
#              and outputs structured KEY=VALUE pairs to stdout.
# Exit Codes:
#   0 - Metrics collected and emitted successfully
#   1 - Invalid command-line argument
#   2 - Missing or unreadable /proc file
#   5 - Malformed or missing expected data fields in /proc
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bash/common.sh
source "${SCRIPT_DIR}/common.sh"

setup_cleanup_trap

# Sampling interval in seconds between the two /proc/stat CPU snapshots.
# Can be overridden via LHM_CPU_SAMPLE_INTERVAL (e.g., 0.01 in fast unit tests).
readonly CPU_SAMPLE_INTERVAL="${LHM_CPU_SAMPLE_INTERVAL:-0.2}"

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

# Validate that a string is a non-negative integer
is_non_negative_int() {
    local val="$1"
    [[ "${val}" =~ ^[0-9]+$ ]]
}

# Read a single snapshot of aggregate CPU counters from /proc/stat
# Arguments:
#   $1 - Prefix for emitted keys (e.g., "CPU1" or "CPU2")
read_cpu_snapshot() {
    local prefix="$1"
    local stat_file="${LHM_PROC_DIR}/stat"

    if [[ ! -r "${stat_file}" ]]; then
        log_error "Cannot read CPU stat file: ${stat_file}"
        exit "${EXIT_PROC_ERROR}"
    fi

    local cpu_line
    cpu_line="$(awk '/^cpu / {print $2, $3, $4, $5, $6, $7, $8, $9; exit}' "${stat_file}")"

    if [[ -z "${cpu_line}" ]]; then
        log_error "Aggregate 'cpu ' line not found in ${stat_file}"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local user nice system idle iowait irq softirq steal
    read -r user nice system idle iowait irq softirq steal <<< "${cpu_line}"

    # Default optional fields (iowait, irq, softirq, steal) to 0 if kernel omits them
    iowait="${iowait:-0}"
    irq="${irq:-0}"
    softirq="${softirq:-0}"
    steal="${steal:-0}"

    local field
    for field in "${user:-}" "${nice:-}" "${system:-}" "${idle:-}" "${iowait}" "${irq}" "${softirq}" "${steal}"; do
        if ! is_non_negative_int "${field}"; then
            log_error "Malformed non-integer CPU counter in ${stat_file}: '${cpu_line}'"
            exit "${EXIT_PARSE_ERROR}"
        fi
    done

    printf "%s_USER=%s\n" "${prefix}" "${user}"
    printf "%s_NICE=%s\n" "${prefix}" "${nice}"
    printf "%s_SYSTEM=%s\n" "${prefix}" "${system}"
    printf "%s_IDLE=%s\n" "${prefix}" "${idle}"
    printf "%s_IOWAIT=%s\n" "${prefix}" "${iowait}"
    printf "%s_IRQ=%s\n" "${prefix}" "${irq}"
    printf "%s_SOFTIRQ=%s\n" "${prefix}" "${softirq}"
    printf "%s_STEAL=%s\n" "${prefix}" "${steal}"
}

# Collect two time-separated CPU snapshots so Python can compute live usage
collect_cpu_metrics() {
    read_cpu_snapshot "CPU1"
    sleep "${CPU_SAMPLE_INTERVAL}"
    read_cpu_snapshot "CPU2"
}

# Collect RAM and Swap counters (in KiB) from /proc/meminfo
collect_memory_metrics() {
    local meminfo_file="${LHM_PROC_DIR}/meminfo"

    if [[ ! -r "${meminfo_file}" ]]; then
        log_error "Cannot read memory info file: ${meminfo_file}"
        exit "${EXIT_PROC_ERROR}"
    fi

    local mem_total mem_free mem_available buffers cached swap_total swap_free
    mem_total="$(awk '/^MemTotal:/ {print $2; exit}' "${meminfo_file}")"
    mem_free="$(awk '/^MemFree:/ {print $2; exit}' "${meminfo_file}")"
    mem_available="$(awk '/^MemAvailable:/ {print $2; exit}' "${meminfo_file}")"
    buffers="$(awk '/^Buffers:/ {print $2; exit}' "${meminfo_file}")"
    cached="$(awk '/^Cached:/ {print $2; exit}' "${meminfo_file}")"
    swap_total="$(awk '/^SwapTotal:/ {print $2; exit}' "${meminfo_file}")"
    swap_free="$(awk '/^SwapFree:/ {print $2; exit}' "${meminfo_file}")"

    # Default Buffers/Cached/Swap to 0 if not present, but require MemTotal and MemFree
    buffers="${buffers:-0}"
    cached="${cached:-0}"
    swap_total="${swap_total:-0}"
    swap_free="${swap_free:-0}"

    # Fallback calculation for MemAvailable on very old kernels if absent
    if [[ -z "${mem_available}" ]]; then
        if is_non_negative_int "${mem_free}" && is_non_negative_int "${buffers}" && is_non_negative_int "${cached}"; then
            mem_available=$(( mem_free + buffers + cached ))
        fi
    fi

    local val
    for val in "${mem_total:-}" "${mem_free:-}" "${mem_available:-}" "${buffers}" "${cached}" "${swap_total}" "${swap_free}"; do
        if ! is_non_negative_int "${val}"; then
            log_error "Malformed or missing memory counter in ${meminfo_file}"
            exit "${EXIT_PARSE_ERROR}"
        fi
    done

    printf "MEM_TOTAL_KB=%s\n" "${mem_total}"
    printf "MEM_FREE_KB=%s\n" "${mem_free}"
    printf "MEM_AVAILABLE_KB=%s\n" "${mem_available}"
    printf "MEM_BUFFERS_KB=%s\n" "${buffers}"
    printf "MEM_CACHED_KB=%s\n" "${cached}"
    printf "SWAP_TOTAL_KB=%s\n" "${swap_total}"
    printf "SWAP_FREE_KB=%s\n" "${swap_free}"
}

main() {
    parse_args "$@"
    # Keep stdout strictly reserved for KEY=VALUE pairs; log status to file only
    _write_to_log_file "INFO" "Starting CPU and memory metric collection."

    printf "TIMESTAMP_UTC=%s\n" "$(get_utc_timestamp)"
    collect_cpu_metrics
    collect_memory_metrics

    _write_to_log_file "INFO" "Completed CPU and memory metric collection."
    exit "${EXIT_SUCCESS}"
}

main "$@"