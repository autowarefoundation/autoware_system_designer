#!/usr/bin/env bash
# Multi-unit environment on Docker: one studio daemon per compute unit of a system.
#   run.sh up host|bridge       build, start and wait until every daemon is healthy
#   run.sh down host|bridge     stop and remove the containers (--volumes drops the built workspace)
#   run.sh compose host|bridge ARGS...   docker compose on the topology (ps, logs, pause, exec, ...)
#   run.sh units [host|bridge]  unit, daemon port and address of each unit
#   run.sh urls [host|bridge]   daemon URL of each unit
#   run.sh ssh UNIT [CMD...]    shell on a unit of the bridge topology, over its sshd
# host    containers of one ECU: host network and IPC, the daemons on the host's loopback
# bridge  separate ECUs: bridge network, each daemon reached through an SSH forward
# A topology is the compose file <ASD_SYSTEM>.<topology>.compose.yaml in ASD_ARCHITECTURE_DIR.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${ASD_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/asd-multi-unit}"
SYSTEM="${ASD_SYSTEM:-TypeAlpha}"
ARCHITECTURE_DIR="${ASD_ARCHITECTURE_DIR:-${HERE}/../../autoware_system_design_examples/ee_architecture}"

export ASD_UID="${ASD_UID:-$(id -u)}"
export ASD_GID="${ASD_GID:-$(id -g)}"
export ASD_KEY_DIR="${STATE_DIR}/keys"
export ASD_DOCKER_DIR="${HERE}"
export ASD_SOURCE_DIR="${ASD_SOURCE_DIR:-$(cd "${HERE}/../../.." && pwd)}"

UNITS=()
declare -A PORT=() ADDRESS=()
SSHD_PORT=""

usage() {
  sed -n '2,11s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2
  exit 2
}

compose() {
  local topology="$1" file
  shift
  file="${ARCHITECTURE_DIR}/${SYSTEM}.${topology}.compose.yaml"
  [[ -f "${file}" ]] || { echo "no topology '${topology}' of ${SYSTEM}: ${file}" >&2; exit 2; }
  docker compose -f "${file}" "$@"
}

# Units of a topology: the services with ASD_COMPUTE_UNIT, their daemon port and network address.
load_units() {
  local unit port address sshd
  UNITS=()
  while read -r unit port address sshd; do
    UNITS+=("${unit}")
    PORT[${unit}]="${port}"
    ADDRESS[${unit}]="${address}"
    [[ "${sshd}" == - ]] || SSHD_PORT="${sshd}"
  done < <(compose "$1" config --format json | python3 -c '
import json, sys
for service in json.load(sys.stdin)["services"].values():
    environment = service.get("environment") or {}
    if "ASD_COMPUTE_UNIT" not in environment:
        continue
    networks = (service.get("networks") or {}).values()
    address = next((n["ipv4_address"] for n in networks if n and n.get("ipv4_address")), "-")
    print(environment["ASD_COMPUTE_UNIT"], environment["ASD_PORT"], address, environment.get("ASD_SSHD_PORT", "-"))
')
  [[ ${#UNITS[@]} -gt 0 ]] || { echo "no unit in topology '$1' of ${SYSTEM}" >&2; exit 1; }
}

ssh_options() {
  local unit="$1"
  printf '%s\n' \
    -p "${SSHD_PORT}" \
    -i "${STATE_DIR}/id_ed25519" \
    -S "${STATE_DIR}/${unit}.sock" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="${STATE_DIR}/known_hosts" \
    -o ConnectTimeout=5 \
    -o ServerAliveInterval=2 \
    -o ServerAliveCountMax=3
}

# Keys of this run: the host's client key, and the key the daemon of main_ecu reaches its peers
# with. The containers read the public halves as authorized_keys.
make_keys() {
  mkdir -p "${ASD_KEY_DIR}"
  chmod 700 "${STATE_DIR}"
  rm -f "${STATE_DIR}/id_ed25519" "${STATE_DIR}/id_ed25519.pub" "${STATE_DIR}/known_hosts"
  rm -f "${ASD_KEY_DIR}/peer_ed25519" "${ASD_KEY_DIR}/peer_ed25519.pub"
  ssh-keygen -q -t ed25519 -N "" -C asd-multi-unit -f "${STATE_DIR}/id_ed25519"
  ssh-keygen -q -t ed25519 -N "" -C asd-multi-unit-peer -f "${ASD_KEY_DIR}/peer_ed25519"
  cat "${STATE_DIR}/id_ed25519.pub" "${ASD_KEY_DIR}/peer_ed25519.pub" >"${ASD_KEY_DIR}/authorized_keys"
}

open_tunnels() {
  local unit options
  for unit in "${UNITS[@]}"; do
    mapfile -t options < <(ssh_options "${unit}")
    ssh "${options[@]}" -f -N -M -o ExitOnForwardFailure=yes \
      -L "127.0.0.1:${PORT[${unit}]}:127.0.0.1:${PORT[${unit}]}" "asd@${ADDRESS[${unit}]}"
  done
}

close_tunnels() {
  local unit options
  for unit in "${UNITS[@]}"; do
    [[ -S "${STATE_DIR}/${unit}.sock" ]] || continue
    mapfile -t options < <(ssh_options "${unit}")
    ssh "${options[@]}" -O exit "asd@${ADDRESS[${unit}]}" 2>/dev/null || rm -f "${STATE_DIR}/${unit}.sock"
  done
}

units() {
  local unit
  for unit in "${UNITS[@]}"; do
    echo "${unit} ${PORT[${unit}]} ${ADDRESS[${unit}]}"
  done
}

urls() {
  local unit
  for unit in "${UNITS[@]}"; do
    echo "${unit} http://127.0.0.1:${PORT[${unit}]}/"
  done
}

command="${1:-}"
[[ -n "${command}" ]] || usage
shift

case "${command}" in
  up)
    topology="${1:-}"
    [[ -n "${topology}" ]] || usage
    load_units "${topology}"
    # Units with an sshd are separate hosts: their daemons are forwarded to the host's loopback.
    if [[ -n "${SSHD_PORT}" ]]; then
      close_tunnels
      make_keys
      compose "${topology}" up --build --wait
      open_tunnels
    else
      compose "${topology}" up --build --wait
    fi
    urls
    ;;
  down)
    topology="${1:-}"
    [[ -n "${topology}" ]] || usage
    shift
    load_units "${topology}"
    [[ -z "${SSHD_PORT}" ]] || close_tunnels
    compose "${topology}" down "$@"
    ;;
  compose)
    topology="${1:-}"
    [[ -n "${topology}" ]] || usage
    shift
    compose "${topology}" "$@"
    ;;
  units)
    load_units "${1:-host}"
    units
    ;;
  urls)
    load_units "${1:-host}"
    urls
    ;;
  ssh)
    unit="${1:-}"
    load_units bridge
    [[ -n "${unit}" && -n "${ADDRESS[${unit}]:-}" ]] || usage
    shift
    mapfile -t options < <(ssh_options "${unit}")
    exec ssh "${options[@]}" "asd@${ADDRESS[${unit}]}" "$@"
    ;;
  *) usage ;;
esac
