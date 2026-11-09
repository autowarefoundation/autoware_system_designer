// Launch: the member tree of a mode's structure as the state monitor shows it.
// Composable nodes sit under their container, the process that holds their
// output, and a tree entry's run state is the severest among its members. The
// same rules give the diagram overlays one live state per node of the mode.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  // Run-state tokens by severity.
  const TOKEN_RANK = { idle: 0, ok: 1, running: 2, warning: 3, failed: 4 };

  // Member states in which the process is not running now, so nothing
  // downstream of it fires: the member's branch is dead.
  const DEAD_STATES = new Set(["stopped", "failed", "done", "respawning"]);

  function worstToken(tokens) {
    let worst = "idle";
    tokens.forEach((token) => {
      if ((TOKEN_RANK[token] || 0) > TOKEN_RANK[worst]) worst = token;
    });
    return worst;
  }

  // "1 warning · 3 running": members per token, severest first; idle members are left out.
  function tokenSummary(tokens) {
    const counts = new Map();
    tokens.forEach((token) => counts.set(token, (counts.get(token) || 0) + 1));
    return Array.from(counts)
      .filter(([token]) => token !== "idle")
      .sort((a, b) => TOKEN_RANK[b[0]] - TOKEN_RANK[a[0]])
      .map(([token, count]) => `${count} ${token}`)
      .join(" · ");
  }

  // A composable node lives only while its container process runs: once the
  // container's record left pending or running, the node takes the container's state.
  function followsContainer(host) {
    return !!host && host.state !== "pending" && host.state !== "running";
  }

  // ROS name of a node entity: its namespace, less a trailing segment equal to the
  // node name, then the name.
  function nodeFqn(entity) {
    const namespace = entity.namespace;
    const parts = (
      Array.isArray(namespace)
        ? namespace.map((part) => String(part).replace(/^\/+|\/+$/g, ""))
        : String(namespace || "").split("/")
    ).filter(Boolean);
    if (parts.length && parts[parts.length - 1] === entity.name) parts.pop();
    return `/${[...parts, entity.name].join("/")}`;
  }

  // Tree entities: each carries key, its selection key shared with Explore, and
  // a node carries member_path, its name as a runtime member; a composable node
  // moves under its container (named by path or ROS name in container_target)
  // and keeps the container in container_path. A node placed at a module's own
  // path gets its own leaf under it.
  function nestComposables(entityList) {
    const { entityKey } = SD.NamespaceTree;
    const containers = new Map();
    entityList.forEach((entity) => {
      if (entity.launcher?.launch_state !== "node_container") return;
      containers.set(entity.path, entity);
      containers.set(nodeFqn(entity), entity);
    });
    return entityList.map((entity) => {
      const key = entityKey(entity);
      if (entity.entity_type !== "node") return { ...entity, key };
      const launcher = entity.launcher || {};
      const container =
        launcher.launch_state === "composable_node" &&
        containers.get(launcher.container_target);
      if (!container)
        return { ...entity, key, path: key, member_path: entity.path };
      return {
        ...entity,
        key,
        path: `${entityKey(container)}/${entity.name}`,
        member_path: entity.path,
        container_path: container.path,
      };
    });
  }

  // Token and label of one tree entity from the live records: a composable
  // node follows its container once that stopped, unless it failed itself.
  function memberState(entity, members) {
    const own = members.get(entity.member_path)?.record;
    const host =
      entity.container_path && members.get(entity.container_path)?.record;
    if (own?.state === "failed" || !followsContainer(host))
      return {
        record: own,
        token: launch.stateToken(own),
        label: launch.stateLabel(own),
      };
    return {
      record: host,
      token: launch.stateToken(host),
      label: `${launch.stateLabel(own)} · container ${launch.stateLabel(host)}`,
    };
  }

  // Live states of a system's launched members, keyed by the node path of the
  // mode's entities (nestComposables output): token, label, `dead` with the
  // reason while the process is down, and the restart counts. Members of other
  // systems' sessions are left out; a node never launched has no entry.
  function memberStates(entities, systemId) {
    const { members, sessions, restartsOf } = launch;
    const states = new Map();
    entities.forEach((entity) => {
      if (entity.entity_type !== "node") return;
      const member = members.get(entity.member_path);
      if (!member || sessions.get(member.unit)?.system !== systemId) return;
      const state = memberState(entity, members);
      const dead = DEAD_STATES.has(state.record?.state) ? state.label : null;
      states.set(entity.member_path, {
        unit: member.unit,
        token: state.token,
        label: state.label,
        dead,
        restarts: restartsOf(entity.member_path),
      });
    });
    return states;
  }

  launch.DEAD_STATES = DEAD_STATES;
  launch.memberState = memberState;
  launch.memberStates = memberStates;
  launch.worstToken = worstToken;
  launch.tokenSummary = tokenSummary;
  launch.followsContainer = followsContainer;
  launch.nodeFqn = nodeFqn;
  launch.nestComposables = nestComposables;
})();
