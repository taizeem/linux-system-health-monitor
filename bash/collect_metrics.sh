#!/usr/bin/env bash
# ==============================================================================
# Script Name: collect_metrics.sh
# Description: Collects raw system metrics from /proc/stat, /proc/meminfo,
#              /proc/net/dev, df, ps, and ping, emitting structured KEY=VALUE
#              pairs to stdout.
# Exit Codes:
#   0 - Metrics collected and emitted successfully
#   1 - Invalid command-line argument
#   2 - Missing or unreadable /proc file
#   5 - Malformed or missing expected data fields in /proc or command output
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bash/common.sh
source "${SCRIPT_DIR}/common.sh"

setup_cleanup_trap

# Configurable parameters (overridable via environment variables for testing)
readonly CPU_SAMPLE_INTERVAL="${LHM_CPU_SAMPLE_INTERVAL:-0.2}"
readonly DISK_MOUNT="${LHM_DISK_MOUNT:-/}"
readonly PING_TARGET="${LHM_PING_TARGET:-8.8.8.8}"
readonly PING_TIMEOUT="${LHM_PING_TIMEOUT:-2}"

# Command overrides for unit testing edge cases
readonly DF_BIN="${LHM_DF_BIN:-df}"
readonly PS_BIN="${LHM_PS_BIN:-ps}"
readonly PING_BIN="${LHM_PING_BIN:-ping}"

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

collect_cpu_metrics() {
    read_cpu_snapshot "CPU1"
    sleep "${CPU_SAMPLE_INTERVAL}"
    read_cpu_snapshot "CPU2"
}

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

    buffers="${buffers:-0}"
    cached="${cached:-0}"
    swap_total="${swap_total:-0}"
    swap_free="${swap_free:-0}"

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

# Collect disk usage (in KiB) for DISK_MOUNT using POSIX single-line df output
collect_disk_metrics() {
    local df_output
    if ! df_output="$("${DF_BIN}" -P -k "${DISK_MOUNT}" 2>/dev/null)"; then
        log_error "Failed to execute df for mount point: ${DISK_MOUNT}"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local disk_line
    disk_line="$(awk 'NR==2 {print $2, $3, $4; exit}' <<< "${df_output}")"

    if [[ -z "${disk_line}" ]]; then
        log_error "Unexpected empty df output for mount point: ${DISK_MOUNT}"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local disk_total disk_used disk_avail
    read -r disk_total disk_used disk_avail <<< "${disk_line}"

    local val
    for val in "${disk_total:-}" "${disk_used:-}" "${disk_avail:-}"; do
        if ! is_non_negative_int "${val}"; then
            log_error "Malformed disk counter from df: '${disk_line}'"
            exit "${EXIT_PARSE_ERROR}"
        fi
    done

    printf "DISK_MOUNT=%s\n" "${DISK_MOUNT}"
    printf "DISK_TOTAL_KB=%s\n" "${disk_total}"
    printf "DISK_USED_KB=%s\n" "${disk_used}"
    printf "DISK_AVAILABLE_KB=%s\n" "${disk_avail}"
}

# Collect total count of running processes via ps
collect_process_metrics() {
    local ps_output
    if ! ps_output="$("${PS_BIN}" -e --no-headers 2>/dev/null)"; then
        log_error "Failed to execute ps command"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local proc_count
    proc_count="$(awk 'NF > 0 {count++} END {print count+0}' <<< "${ps_output}")"

    if ! is_non_negative_int "${proc_count}" || [[ "${proc_count}" -eq 0 ]]; then
        log_error "Invalid running process count: '${proc_count}'"
        exit "${EXIT_PARSE_ERROR}"
    fi

    printf "RUNNING_PROCESSES=%s\n" "${proc_count}"
}

# Collect network interface traffic from /proc/net/dev and reachability via ping
collect_network_metrics() {
    local net_dev_file="${LHM_PROC_DIR}/net/dev"

    if [[ ! -r "${net_dev_file}" ]]; then
        log_error "Cannot read network interface file: ${net_dev_file}"
        exit "${EXIT_PROC_ERROR}"
    fi

    local net_totals
    net_totals="$(awk -F'[: ]+' '
        NR > 2 && $0 ~ /:/ {
            iface = $1
            if (iface == "") { iface = $2; rx = $3; tx = $11 }
            else { rx = $2; tx = $10 }
            if (iface != "lo") {
                if (rx !~ /^[0-9]+$/ || tx !~ /^[0-9]+$/) {
                    print "INVALID"
                    exit
                }
                total_rx += rx
                total_tx += tx
            }
        }
        END {
            printf "%d %d\n", total_rx + 0, total_tx + 0
        }
    ' "${net_dev_file}")"

    if [[ "${net_totals}" == INVALID* ]]; then
        log_error "Malformed byte counter in ${net_dev_file}"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local rx_bytes tx_bytes
    read -r rx_bytes tx_bytes <<< "${net_totals}"

    if ! is_non_negative_int "${rx_bytes:-}" || ! is_non_negative_int "${tx_bytes:-}"; then
        log_error "Failed to parse network byte counters from ${net_dev_file}"
        exit "${EXIT_PARSE_ERROR}"
    fi

    local net_status="Disconnected"
    if "${PING_BIN}" -c 1 -W "${PING_TIMEOUT}" "${PING_TARGET}" >/dev/null 2>&1; then
        net_status="Connected"
    fi

    printf "NET_RX_BYTES=%s\n" "${rx_bytes}"
    printf "NET_TX_BYTES=%s\n" "${tx_bytes}"
    printf "NETWORK_STATUS=%s\n" "${net_status}"
}

main() {
    parse_args "$@"
    _write_to_log_file "INFO" "Starting full system metric collection."

    printf "TIMESTAMP_UTC=%s\n" "$(get_utc_timestamp)"
    collect_cpu_metrics
    collect_memory_metrics
    collect_disk_metrics
    collect_process_metrics
    collect_network_metrics

    _write_to_log_file "INFO" "Completed full system metric collection."
    exit "${EXIT_SUCCESS}"
}

main "$@"