#!/usr/bin/env bash
# Multi-unit environment on Docker: one studio daemon per compute unit of a system.
#   run.sh up TOPOLOGY              build, start and wait until every daemon is healthy
#   run.sh down TOPOLOGY            stop and remove the containers (--volumes drops the built workspace)
#   run.sh compose TOPOLOGY ARGS... docker compose on the topology (ps, logs, pause, exec, ...)
#   run.sh topologies               topologies of the system
#   run.sh units TOPOLOGY           unit, service, daemon port, address and role of each unit
#   run.sh url TOPOLOGY             URL of the daemon that serves every unit
#   run.sh ssh TOPOLOGY SERVICE [CMD...]  shell in a service with an sshd
# A topology is the compose file <ASD_SYSTEM>.<TOPOLOGY>.compose.yaml in ASD_ARCHITECTURE_DIR,
# e.g. host, bridge, two_ecus of TypeAlpha.
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

# Binding of each unit, as the topology's compose file states it: the service running its daemon,
# the daemon port, the address and sshd port of its network namespace ("-" without), and its role,
# "main" for the unit whose daemon has the others as peers.
UNITS=()
declare -A SERVICE=() PORT=() ADDRESS=() SSHD=() ROLE=()

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

# The one place the bindings are read. A service in another's network namespace has that
# service's address and sshd.
load_units() {
  local unit service port address sshd role
  UNITS=()
  while read -r unit service port address sshd role; do
    UNITS+=("${unit}")
    SERVICE[${unit}]="${service}"
    PORT[${unit}]="${port}"
    ADDRESS[${unit}]="${address}"
    SSHD[${unit}]="${sshd}"
    ROLE[${unit}]="${role}"
  done < <(compose "$1" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
def host(name):
    mode = services[name].get("network_mode") or ""
    return host(mode[len("service:"):]) if mode.startswith("service:") else name
for name, service in services.items():
    environment = service.get("environment") or {}
    if "ASD_COMPUTE_UNIT" not in environment:
        continue
    owner = services[host(name)]
    networks = (owner.get("networks") or {}).values()
    address = next((n["ipv4_address"] for n in networks if n and n.get("ipv4_address")), "-")
    sshd = (owner.get("environment") or {}).get("ASD_SSHD_PORT", "-")
    role = "main" if environment.get("ASD_PEERS") else "peer"
    print(environment["ASD_COMPUTE_UNIT"], name, environment["ASD_PORT"], address, sshd, role)
')
  [[ ${#UNITS[@]} -gt 0 ]] || { echo "no unit in topology '$1' of ${SYSTEM}" >&2; exit 1; }
}

main_unit() {
  local unit
  for unit in "${UNITS[@]}"; do
    [[ "${ROLE[${unit}]}" == main ]] && { echo "${unit}"; return; }
  done
  echo "no unit of the topology has the others as peers" >&2
  exit 1
}

ssh_options() {
  local unit="$1"
  printf '%s\n' \
    -p "${SSHD[${unit}]}" \
    -i "${STATE_DIR}/id_ed25519" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="${STATE_DIR}/known_hosts" \
    -o ConnectTimeout=5 \
    -o ServerAliveInterval=2 \
    -o ServerAliveCountMax=3
}

# Keys of this run: the host's client key, and the key the main daemon reaches its peers with.
# The containers read the public halves as authorized_keys.
make_keys() {
  mkdir -p "${ASD_KEY_DIR}"
  chmod 700 "${STATE_DIR}"
  rm -f "${STATE_DIR}/id_ed25519" "${STATE_DIR}/id_ed25519.pub" "${STATE_DIR}/known_hosts"
  rm -f "${ASD_KEY_DIR}/peer_ed25519" "${ASD_KEY_DIR}/peer_ed25519.pub"
  ssh-keygen -q -t ed25519 -N "" -C asd-multi-unit -f "${STATE_DIR}/id_ed25519"
  ssh-keygen -q -t ed25519 -N "" -C asd-multi-unit-peer -f "${ASD_KEY_DIR}/peer_ed25519"
  cat "${STATE_DIR}/id_ed25519.pub" "${ASD_KEY_DIR}/peer_ed25519.pub" >"${ASD_KEY_DIR}/authorized_keys"
}

# The main daemon listens on its container's loopback; a forward brings it to the host's on the same port.
open_tunnel() {
  local unit options
  unit="$(main_unit)"
  [[ "${SSHD[${unit}]}" != - ]] || return 0
  mapfile -t options < <(ssh_options "${unit}")
  ssh "${options[@]}" -S "${STATE_DIR}/main.sock" -f -N -M -o ExitOnForwardFailure=yes \
    -L "127.0.0.1:${PORT[${unit}]}:127.0.0.1:${PORT[${unit}]}" "asd@${ADDRESS[${unit}]}"
}

close_tunnel() {
  [[ -S "${STATE_DIR}/main.sock" ]] || return 0
  ssh -S "${STATE_DIR}/main.sock" -O exit main 2>/dev/null || rm -f "${STATE_DIR}/main.sock"
}

command="${1:-}"
[[ -n "${command}" ]] || usage
shift

if [[ "${command}" == topologies ]]; then
  for file in "${ARCHITECTURE_DIR}/${SYSTEM}".*.compose.yaml; do
    [[ -f "${file}" ]] || continue
    name="$(basename "${file}" .compose.yaml)"
    echo "${name#"${SYSTEM}".}"
  done
  exit 0
fi

topology="${1:-}"
[[ -n "${topology}" ]] || usage
shift

case "${command}" in
  up)
    load_units "${topology}"
    close_tunnel
    make_keys
    compose "${topology}" up --build --wait
    open_tunnel
    echo "http://127.0.0.1:${PORT[$(main_unit)]}/"
    ;;
  down)
    close_tunnel
    mkdir -p "${ASD_KEY_DIR}"
    compose "${topology}" down "$@"
    ;;
  compose)
    mkdir -p "${ASD_KEY_DIR}"
    compose "${topology}" "$@"
    ;;
  units)
    load_units "${topology}"
    for unit in "${UNITS[@]}"; do
      echo "${unit} ${SERVICE[${unit}]} ${PORT[${unit}]} ${ADDRESS[${unit}]} ${ROLE[${unit}]}"
    done
    ;;
  url)
    load_units "${topology}"
    echo "http://127.0.0.1:${PORT[$(main_unit)]}/"
    ;;
  ssh)
    service="${1:-}"
    load_units "${topology}"
    [[ -n "${service}" ]] || usage
    shift
    for unit in "${UNITS[@]}"; do
      [[ "${SERVICE[${unit}]}" == "${service}" && "${SSHD[${unit}]}" != - ]] || continue
      mapfile -t options < <(ssh_options "${unit}")
      exec ssh "${options[@]}" "asd@${ADDRESS[${unit}]}" "$@"
    done
    echo "no service '${service}' with an sshd in topology '${topology}'" >&2
    exit 2
    ;;
  *) usage ;;
esac
