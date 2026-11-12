#!/usr/bin/env bash
# Multi-unit environment on Docker: one studio daemon per compute unit of a system.
#   run.sh up TOPOLOGY              build, start and wait until every daemon is healthy
#   run.sh down TOPOLOGY            stop and remove the containers (--volumes drops the built workspace)
#   run.sh compose TOPOLOGY ARGS... docker compose on the topology (ps, logs, pause, exec, ...)
#   run.sh topologies               topologies of the system
#   run.sh units TOPOLOGY           unit, service, daemon port, address, device, network namespace and role of each unit
#   run.sh url TOPOLOGY             URL of the daemon that serves every unit
#   run.sh ssh TOPOLOGY SERVICE [CMD...]  shell in a service with an sshd
# A topology is the compose file <ASD_SYSTEM>.<TOPOLOGY>.compose.yaml in ASD_ARCHITECTURE_DIR,
# e.g. host, bridge, two_ecus of TypeAlpha. It states where each unit runs; which daemon is given
# which peers follows from that.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${ASD_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/asd-multi-unit}"
SYSTEM="${ASD_SYSTEM:-TypeAlpha}"
ARCHITECTURE_DIR="${ASD_ARCHITECTURE_DIR:-${HERE}/../autoware_system_designer_demo_systems/ee_architecture}"

export ASD_UID="${ASD_UID:-$(id -u)}"
export ASD_GID="${ASD_GID:-$(id -g)}"
export ASD_KEY_DIR="${STATE_DIR}/keys"
export ASD_DOCKER_DIR="${HERE}"
export ASD_SOURCE_DIR="${ASD_SOURCE_DIR:-$(cd "${HERE}/../.." && pwd)}"

# Binding of each unit, as the topology's compose file states it: the service running its daemon,
# the daemon port, the address and sshd port of its network namespace ("-" without), the device and
# the service holding that namespace, and its role, "main" for the units of the daemon that has the
# others as peers.
UNITS=()
declare -A SERVICE=() PORT=() ADDRESS=() SSHD=() DEVICE=() NETNS=() ROLE=()

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
# service's address, sshd and device; the device is the hostname of the namespace, "host" on the
# host network. The peers of the main daemon follow from the bindings: every unit of another
# daemon at that daemon's port on the loopback, forwarded from its address when its device is
# another one.
load_units() {
  local unit service port address sshd device netns role main
  UNITS=()
  while read -r unit service port address sshd device netns role; do
    UNITS+=("${unit}")
    SERVICE[${unit}]="${service}"
    PORT[${unit}]="${port}"
    ADDRESS[${unit}]="${address}"
    SSHD[${unit}]="${sshd}"
    DEVICE[${unit}]="${device}"
    NETNS[${unit}]="${netns}"
    ROLE[${unit}]="${role}"
  done < <(ASD_PEERS="" ASD_PEER_SSH="" compose "$1" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
def host(name):
    mode = services[name].get("network_mode") or ""
    return host(mode[len("service:"):]) if mode.startswith("service:") else name
for name, service in services.items():
    environment = service.get("environment") or {}
    netns = host(name)
    owner = services[netns]
    networks = (owner.get("networks") or {}).values()
    address = next((n["ipv4_address"] for n in networks if n and n.get("ipv4_address")), "-")
    sshd = (owner.get("environment") or {}).get("ASD_SSHD_PORT", "-")
    device = owner.get("hostname") or ("host" if owner.get("network_mode") == "host" else netns)
    role = "main" if environment.get("ASD_MAIN") == "true" else "peer"
    for unit in (environment.get("ASD_COMPUTE_UNITS") or "").split():
        print(unit, name, environment["ASD_PORT"], address, sshd, device, netns, role)
')
  [[ ${#UNITS[@]} -gt 0 ]] || { echo "no unit in topology '$1' of ${SYSTEM}" >&2; exit 1; }
  main="$(main_unit)"
  ASD_PEERS=""
  ASD_PEER_SSH=""
  for unit in "${UNITS[@]}"; do
    [[ "${SERVICE[${unit}]}" != "${SERVICE[${main}]}" ]] || continue
    ASD_PEERS+=" ${unit}=http://127.0.0.1:${PORT[${unit}]}"
    [[ "${DEVICE[${unit}]}" == "${DEVICE[${main}]}" ]] || ASD_PEER_SSH+=" ${unit}=${ADDRESS[${unit}]}"
  done
  export ASD_PEERS ASD_PEER_SSH
}

# The first unit of the one daemon that has the others as peers.
main_unit() {
  local unit main="" service=""
  for unit in "${UNITS[@]}"; do
    [[ "${ROLE[${unit}]}" == main ]] || continue
    if [[ -n "${service}" && "${SERVICE[${unit}]}" != "${service}" ]]; then
      echo "services ${service} and ${SERVICE[${unit}]} both have ASD_MAIN" >&2
      exit 1
    fi
    [[ -n "${main}" ]] || main="${unit}"
    service="${SERVICE[${unit}]}"
  done
  [[ -n "${main}" ]] || { echo "no service of the topology has ASD_MAIN" >&2; exit 1; }
  echo "${main}"
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
    load_units "${topology}"
    close_tunnel
    mkdir -p "${ASD_KEY_DIR}"
    compose "${topology}" down "$@"
    ;;
  compose)
    load_units "${topology}"
    mkdir -p "${ASD_KEY_DIR}"
    compose "${topology}" "$@"
    ;;
  units)
    load_units "${topology}"
    for unit in "${UNITS[@]}"; do
      echo "${unit} ${SERVICE[${unit}]} ${PORT[${unit}]} ${ADDRESS[${unit}]} ${DEVICE[${unit}]} ${NETNS[${unit}]} ${ROLE[${unit}]}"
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
