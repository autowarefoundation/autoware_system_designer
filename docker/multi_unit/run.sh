#!/usr/bin/env bash
# Multi-unit environment on Docker: one studio daemon per compute unit of the demo system.
#   run.sh up host|bridge       build, start and wait until every daemon is healthy
#   run.sh down host|bridge     stop and remove the containers (--volumes drops the built workspace)
#   run.sh compose host|bridge ARGS...   docker compose on the topology (ps, logs, pause, exec, ...)
#   run.sh urls                 daemon URL of each unit
#   run.sh ssh UNIT [CMD...]    shell on a unit of the bridge topology, over its sshd
# host    containers of one ECU: host network and IPC, the daemons on the host's loopback
# bridge  separate ECUs: bridge network, each daemon reached through an SSH forward
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${ASD_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/asd-multi-unit}"
SUBNET_PREFIX="${ASD_SUBNET_PREFIX:-172.30.87}"
SSHD_PORT=2222

UNITS=(main_ecu sub_ecu_1 sub_ecu_2)
declare -A PORT=(
  [main_ecu]="${ASD_PORT_MAIN_ECU:-8781}"
  [sub_ecu_1]="${ASD_PORT_SUB_ECU_1:-8782}"
  [sub_ecu_2]="${ASD_PORT_SUB_ECU_2:-8783}"
)
declare -A ADDRESS=(
  [main_ecu]="${SUBNET_PREFIX}.11"
  [sub_ecu_1]="${SUBNET_PREFIX}.12"
  [sub_ecu_2]="${SUBNET_PREFIX}.13"
)

export ASD_UID="${ASD_UID:-$(id -u)}"
export ASD_GID="${ASD_GID:-$(id -g)}"
export ASD_KEY_DIR="${STATE_DIR}/keys"

usage() {
  sed -n '2,9s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2
  exit 2
}

compose() {
  local topology="$1"
  shift
  docker compose -f "${HERE}/compose.${topology}.yaml" "$@"
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

# Client key of this run; the containers read its public half as authorized_keys.
make_keys() {
  mkdir -p "${ASD_KEY_DIR}"
  chmod 700 "${STATE_DIR}"
  rm -f "${STATE_DIR}/id_ed25519" "${STATE_DIR}/id_ed25519.pub" "${STATE_DIR}/known_hosts"
  ssh-keygen -q -t ed25519 -N "" -C asd-multi-unit -f "${STATE_DIR}/id_ed25519"
  cp "${STATE_DIR}/id_ed25519.pub" "${ASD_KEY_DIR}/authorized_keys"
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
    case "${topology}" in
      host) compose host up --build --wait ;;
      bridge)
        close_tunnels
        make_keys
        compose bridge up --build --wait
        open_tunnels
        ;;
      *) usage ;;
    esac
    urls
    ;;
  down)
    topology="${1:-}"
    [[ "${topology}" == host || "${topology}" == bridge ]] || usage
    shift
    [[ "${topology}" == host ]] || close_tunnels
    compose "${topology}" down "$@"
    ;;
  compose)
    topology="${1:-}"
    [[ "${topology}" == host || "${topology}" == bridge ]] || usage
    shift
    compose "${topology}" "$@"
    ;;
  urls) urls ;;
  ssh)
    unit="${1:-}"
    [[ -n "${unit}" && -n "${ADDRESS[${unit}]:-}" ]] || usage
    shift
    mapfile -t options < <(ssh_options "${unit}")
    exec ssh "${options[@]}" "asd@${ADDRESS[${unit}]}" "$@"
    ;;
  *) usage ;;
esac
