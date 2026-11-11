ARG ROS_DISTRO=humble
FROM ros:${ROS_DISTRO}-ros-base

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      iproute2 \
      openssh-server \
      python3-jinja2 \
      python3-jsonschema \
      python3-yaml \
      ros-${ROS_DISTRO}-demo-nodes-cpp \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user "asd" on the host user's ids, so files in shared memory and volumes stay the host user's.
RUN set -eu; \
    existing_group="$(getent group "${GID}" | cut -d: -f1)"; \
    if [ -z "${existing_group}" ]; then groupadd -g "${GID}" asd; \
    elif [ "${existing_group}" != asd ]; then groupmod -n asd "${existing_group}"; fi; \
    existing_user="$(getent passwd "${UID}" | cut -d: -f1)"; \
    if [ -z "${existing_user}" ]; then useradd -m -u "${UID}" -g "${GID}" -s /bin/bash asd; \
    elif [ "${existing_user}" != asd ]; then usermod -l asd -d /home/asd -m -g "${GID}" -s /bin/bash "${existing_user}"; fi; \
    usermod -p '*' asd; \
    mkdir -p /ws/src /ws/out /run/asd; \
    chown asd:asd /ws/out /run/asd

COPY entrypoint.sh /usr/local/bin/asd-entrypoint
RUN chmod 0755 /usr/local/bin/asd-entrypoint

USER asd
WORKDIR /ws
ENV XDG_RUNTIME_DIR=/run/asd

ENTRYPOINT ["/usr/local/bin/asd-entrypoint"]
CMD ["serve"]
