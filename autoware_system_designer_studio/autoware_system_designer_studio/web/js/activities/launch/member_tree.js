// Launch: the member tree of a mode's structure as the state monitor shows it.
// Composable nodes sit under their container, the process that holds their
// output, and a tree entry's run state is the severest among its members.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  // Run-state tokens by severity.
  const TOKEN_RANK = { idle: 0, ok: 1, running: 2, warning: 3, failed: 4 };

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

  // Tree entities: each carries member_path, its name as a runtime member; a
  // composable node moves under its container (named by path or ROS name in
  // container_target) and keeps the container in container_path.
  function nestComposables(entityList) {
    const containers = new Map();
    entityList.forEach((entity) => {
      if (entity.launcher?.launch_state !== "node_container") return;
      containers.set(entity.path, entity);
      containers.set(nodeFqn(entity), entity);
    });
    return entityList.map((entity) => {
      const launcher = entity.launcher || {};
      const container =
        launcher.launch_state === "composable_node" &&
        containers.get(launcher.container_target);
      if (!container) return { ...entity, member_path: entity.path };
      const segment = entity.path.split("/").filter(Boolean).pop();
      return {
        ...entity,
        path: `${container.path}/${segment}`,
        member_path: entity.path,
        container_path: container.path,
      };
    });
  }

  launch.worstToken = worstToken;
  launch.tokenSummary = tokenSummary;
  launch.followsContainer = followsContainer;
  launch.nodeFqn = nodeFqn;
  launch.nestComposables = nestComposables;
})();
