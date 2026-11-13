#!/usr/bin/env bash
# Container entry of the multi-unit environment.
#   build   colcon build of ASD_PACKAGES and their dependencies from /ws/src into /ws/out
#   serve   studio daemon of the units ASD_COMPUTE_UNITS on ASD_PORT, with sshd when ASD_SSHD_PORT
#           is set; with ASD_MAIN=true it has the daemons of the other units as peers, bound by
#           the site file in /run/asd/site; ASD_RMW_IMPLEMENTATION selects the RMW implementation
set -eo pipefail

OUT=/ws/out
SITE=/run/asd/site
PACKAGES="${ASD_PACKAGES:-autoware_system_designer_studio autoware_system_designer_demo_systems}"

# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"
# shellcheck disable=SC1091
source /opt/build_depends/setup.bash

build() {
  # shellcheck disable=SC2086
  colcon --log-base "${OUT}/log" build \
    --base-paths /ws/src \
    --build-base "${OUT}/build" \
    --install-base "${OUT}/install" \
    --packages-up-to ${PACKAGES} \
    --event-handlers console_cohesion+ \
    --cmake-args -DBUILD_TESTING=OFF
}

# sshd of the unprivileged user: key login as "asd" only, host key made once per container.
start_sshd() {
  local dir=/run/asd/sshd
  mkdir -p "${dir}"
  [[ -f "${dir}/host_key" ]] || ssh-keygen -q -t ed25519 -N "" -f "${dir}/host_key"
  cat >"${dir}/sshd_config" <<CONFIG
Port ${ASD_SSHD_PORT}
HostKey ${dir}/host_key
PidFile ${dir}/sshd.pid
AuthorizedKeysFile ${SITE}/authorized_keys
StrictModes no
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowTcpForwarding local
X11Forwarding no
CONFIG
  /usr/sbin/sshd -f "${dir}/sshd_config" -E "${dir}/sshd.log"
}

# SSH client configuration of the daemon's user: the key and host key handling its peers' sshd take.
write_ssh_config() {
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  cat >"${HOME}/.ssh/config" <<CONFIG
Host *
  IdentityFile ${SITE}/peer_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /run/asd/known_hosts
  ConnectTimeout 5
CONFIG
}

serve() {
  : "${ASD_COMPUTE_UNITS:?compute units this daemon serves}"
  if [[ ! -f "${OUT}/install/setup.bash" ]]; then
    echo "no workspace in ${OUT}; run the build service first" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "${OUT}/install/setup.bash"
  if [[ -n "${ASD_RMW_IMPLEMENTATION:-}" ]]; then
    export RMW_IMPLEMENTATION="${ASD_RMW_IMPLEMENTATION}"
  fi
  if [[ -n "${ASD_SSHD_PORT:-}" ]]; then
    start_sshd
  fi
  local unit units=() peers=()
  for unit in ${ASD_COMPUTE_UNITS}; do
    units+=(--compute-unit "${unit}")
  done
  if [[ "${ASD_MAIN:-}" == true ]]; then
    write_ssh_config
    peers=(--site-file "${SITE}/peers.site.yaml")
  fi
  exec autoware-system-designer-studio serve \
    --install-root "${OUT}/install" \
    --port "${ASD_PORT:?port of this daemon}" \
    "${units[@]}" \
    "${peers[@]}"
}

case "${1:-serve}" in
  build) build ;;
  serve) serve ;;
  *) exec "$@" ;;
esac
