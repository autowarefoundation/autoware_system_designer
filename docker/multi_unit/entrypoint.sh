#!/usr/bin/env bash
# Container entry of the multi-unit environment.
#   build   colcon build of ASD_PACKAGES and their dependencies from /ws/src into /ws/out
#   serve   studio daemon of ASD_COMPUTE_UNIT on ASD_PORT, with sshd when ASD_SSHD_PORT is set
set -eo pipefail

OUT=/ws/out
PACKAGES="${ASD_PACKAGES:-autoware_system_designer_studio multi_unit_demo}"

# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"

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

# sshd of the unprivileged user: key login as "asd" only, host key made at each start.
start_sshd() {
  local dir=/run/asd/sshd
  mkdir -p "${dir}"
  rm -f "${dir}/host_key" "${dir}/host_key.pub"
  ssh-keygen -q -t ed25519 -N "" -f "${dir}/host_key"
  cat >"${dir}/sshd_config" <<CONFIG
Port ${ASD_SSHD_PORT}
HostKey ${dir}/host_key
PidFile ${dir}/sshd.pid
AuthorizedKeysFile ${ASD_AUTHORIZED_KEYS:-/run/asd/keys/authorized_keys}
StrictModes no
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowTcpForwarding local
X11Forwarding no
CONFIG
  /usr/sbin/sshd -f "${dir}/sshd_config" -E "${dir}/sshd.log"
}

serve() {
  : "${ASD_COMPUTE_UNIT:?compute unit this daemon serves}"
  if [[ ! -f "${OUT}/install/setup.bash" ]]; then
    echo "no workspace in ${OUT}; run the build service first" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "${OUT}/install/setup.bash"
  if [[ -n "${ASD_SSHD_PORT:-}" ]]; then
    start_sshd
  fi
  exec autoware-system-designer-studio serve \
    --install-root "${OUT}/install" \
    --port "${ASD_PORT:-8780}" \
    --compute-unit "${ASD_COMPUTE_UNIT}"
}

case "${1:-serve}" in
  build) build ;;
  serve) serve ;;
  *) exec "$@" ;;
esac
