// Replay: the scope of a deterministic replay and its boundary, read from a
// mode's system structure as the runtime's replay does, the bag topics matched
// against the boundary inputs, and the replay configuration they give.
(function () {
  const SD = window.SD;
  const replay = ((SD.activities ||= {}).replay ||= {});

  // Topics every rcl process carries; no boundary holds them.
  const INFRA_TOPICS = new Set(["/rosout", "/parameter_events", "/clock"]);
  const REPLAY_COMMAND = "autoware-system-designer-replay";
  const CONFIG_FILE = "replay.yaml";

  function resolveTopic(port) {
    const topic = port.topic;
    if (Array.isArray(topic)) {
      const parts = topic
        .filter(Boolean)
        .map((part) => String(part).replace(/^\/+|\/+$/g, ""));
      return parts.length ? `/${parts.join("/")}` : null;
    }
    if (typeof topic === "string" && topic)
      return topic.startsWith("/") ? topic : `/${topic}`;
    return null;
  }

  function isTopicPort(port) {
    const type = String(port.msg_type || "");
    return !type.includes("/srv/") && !type.includes("/action/");
  }

  function fqn(entity) {
    const namespace = Array.isArray(entity.namespace)
      ? entity.namespace.join("/")
      : String(entity.namespace || "");
    const parts = namespace.split("/").filter(Boolean);
    if (parts[parts.length - 1] === entity.name) parts.pop();
    return `/${[...parts, entity.name].join("/")}`;
  }

  // Launched nodes of a structure in design order, each with its topics by
  // direction and its service ports.
  function nodes(root) {
    const list = [];
    const walk = (entity) => {
      if (entity.entity_type === "node" && entity.launcher) {
        const node = {
          path: entity.path || fqn(entity),
          fqn: fqn(entity),
          unit: entity.compute_unit || "",
          launchState: entity.launcher.launch_state || "",
          containerTarget: entity.launcher.container_target || "",
          inputs: new Map(),
          outputs: new Map(),
          services: [],
        };
        [
          ["in_ports", node.inputs, "client"],
          ["out_ports", node.outputs, "server"],
        ].forEach(([side, topics, fallbackRole]) => {
          (entity[side] || []).forEach((port) => {
            const name = resolveTopic(port);
            if (name === null) return;
            const type = String(port.msg_type || "");
            if (isTopicPort(port)) {
              if (!topics.has(name)) topics.set(name, type);
              return;
            }
            const role = ["client", "server"].includes(port.role)
              ? port.role
              : fallbackRole;
            node.services.push({ name, type, role });
          });
        });
        list.push(node);
      }
      (entity.children || []).forEach(walk);
    };
    if (root) walk(root);
    return list;
  }

  function under(path, pattern) {
    const base = pattern.replace(/\/+$/, "");
    return base === "" || path === base || path.startsWith(`${base}/`);
  }

  // Nodes at or under the patterns, the containers of the selected composable
  // nodes, and the patterns that select nothing.
  function selectScope(nodeList, patterns) {
    const selected = nodeList.filter((node) =>
      patterns.some((pattern) => under(node.path, pattern)),
    );
    const targets = new Set(
      selected
        .filter(
          (node) =>
            node.launchState === "composable_node" && node.containerTarget,
        )
        .map((node) => node.containerTarget),
    );
    return {
      nodes: selected.map((node) => node.path),
      containers: nodeList
        .filter(
          (node) =>
            node.launchState === "node_container" && targets.has(node.fqn),
        )
        .map((node) => node.path),
      unmatched: patterns.filter(
        (pattern) => !nodeList.some((node) => under(node.path, pattern)),
      ),
    };
  }

  // Fewest paths of the namespace tree that select exactly `selected` (a Set
  // of node paths): an entry whose every node is selected stands for them.
  function patternsOf(nodeList, selected) {
    const patterns = [];
    const visit = (prefix, members) => {
      if (!members.length) return;
      if (members.every((node) => selected.has(node.path))) {
        patterns.push(prefix || "/");
        return;
      }
      const depth = prefix.split("/").filter(Boolean).length;
      const groups = new Map();
      members.forEach((node) => {
        const segments = node.path.split("/").filter(Boolean);
        if (segments.length <= depth) return;
        const child = `/${segments.slice(0, depth + 1).join("/")}`;
        if (!groups.has(child)) groups.set(child, []);
        groups.get(child).push(node);
      });
      groups.forEach((group, child) => visit(child, group));
    };
    visit("", nodeList);
    return patterns;
  }

  // Where the scope meets the rest of the system: inputs a node outside
  // publishes (or none does), the scope's outputs, and its service ports.
  function boundary(nodeList, scopePaths) {
    const inside = new Set(scopePaths);
    const publishers = new Map();
    const subscribers = new Map();
    const push = (map, key, value) => {
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(value);
    };
    nodeList.forEach((node) => {
      node.outputs.forEach((_, topic) => push(publishers, topic, node.path));
      node.inputs.forEach((_, topic) => push(subscribers, topic, node.path));
    });

    const inputs = new Map();
    const outputs = new Map();
    const scoped = nodeList.filter((node) => inside.has(node.path));
    scoped.forEach((node) => {
      node.inputs.forEach((type, topic) => {
        if (INFRA_TOPICS.has(topic)) return;
        const sources = publishers.get(topic) || [];
        const outside = sources.filter((path) => !inside.has(path));
        if (sources.length && !outside.length) return;
        if (!inputs.has(topic))
          inputs.set(topic, {
            topic,
            type,
            subscribers: [],
            publishers: outside,
            scopePublishers: sources.filter((path) => inside.has(path)),
          });
        inputs.get(topic).subscribers.push(node.path);
      });
      node.outputs.forEach((type, topic) => {
        if (INFRA_TOPICS.has(topic)) return;
        if (!outputs.has(topic)) {
          const readers = subscribers.get(topic) || [];
          const scopeSubscribers = readers.filter((path) => inside.has(path));
          const outsideSubscribers = readers.filter(
            (path) => !inside.has(path),
          );
          outputs.set(topic, {
            topic,
            type,
            publishers: [],
            scopeSubscribers,
            outsideSubscribers,
            boundary: outsideSubscribers.length > 0 || !scopeSubscribers.length,
          });
        }
        outputs.get(topic).publishers.push(node.path);
      });
    });

    const services = [];
    scoped.forEach((node) => {
      node.services.forEach(({ name, type, role }) => {
        const other = role === "client" ? "server" : "client";
        services.push({
          name,
          type,
          node: node.path,
          role,
          scopePeers: scoped
            .filter(
              (peer) =>
                peer !== node &&
                peer.services.some((s) => s.name === name && s.role === other),
            )
            .map((peer) => peer.path),
        });
      });
    });
    const byTopic = (a, b) => a.topic.localeCompare(b.topic);
    return {
      inputs: [...inputs.values()].sort(byTopic),
      outputs: [...outputs.values()].sort(byTopic),
      services: services.sort(
        (a, b) => a.name.localeCompare(b.name) || a.node.localeCompare(b.node),
      ),
    };
  }

  function unquote(value) {
    const text = value.trim();
    return /^(".*"|'.*')$/.test(text) ? text.slice(1, -1) : text;
  }

  // Topics of a rosbag2 `metadata.yaml`: name -> { type, count }.
  function parseBagMetadata(text) {
    if (!/rosbag2_bagfile_information\s*:/.test(text))
      throw new Error("not a rosbag2 metadata.yaml");
    const topics = new Map();
    let current = null;
    let inTopics = false;
    text.split(/\r?\n/).forEach((line) => {
      if (/^\s*topics_with_message_count\s*:/.test(line)) {
        inTopics = true;
        return;
      }
      if (!inTopics) return;
      if (/^ {0,2}\S/.test(line) && !/^\s*-/.test(line)) {
        inTopics = false;
        return;
      }
      const entry = line.match(/^\s*(?:-\s*)?([a-z_]+)\s*:\s*(.*)$/);
      if (!entry) return;
      const [, key, value] = entry;
      if (key === "name") {
        current = { type: "", count: 0 };
        topics.set(unquote(value), current);
      } else if (key === "type" && current) current.type = unquote(value);
      else if (key === "message_count" && current)
        current.count = Number(value) || 0;
    });
    return topics;
  }

  // Each boundary input against the bag (null before one is loaded), the remap
  // (recorded topic -> fed topic) and the accepted names:
  //   fed | remapped | accepted | type (recorded with another type) |
  //   unpublished (no publisher in the design, not in the bag) | missing | unknown (no bag)
  // with the bag topics of the input's type that no input takes as `candidates`.
  function matchInputs(bound, bag, remap, accept) {
    const source = new Map(
      Object.entries(remap).map(([recorded, fed]) => [fed, recorded]),
    );
    const taken = new Set(bound.inputs.map((input) => input.topic));
    return bound.inputs.map((input) => {
      const recordedName = source.get(input.topic) || input.topic;
      const recorded = bag ? bag.get(recordedName) : null;
      const candidates = bag
        ? [...bag.entries()]
            .filter(
              ([name, topic]) =>
                topic.type === input.type &&
                (!taken.has(name) || name === input.topic),
            )
            .map(([name]) => name)
            .sort()
        : [];
      let status;
      if (!bag) status = accept.has(input.topic) ? "accepted" : "unknown";
      else if (!recorded)
        status = accept.has(input.topic)
          ? "accepted"
          : input.publishers.length
            ? "missing"
            : "unpublished";
      else if (input.type && recorded.type !== input.type) status = "type";
      else status = recordedName === input.topic ? "fed" : "remapped";
      return {
        ...input,
        status,
        recordedName: recorded ? recordedName : "",
        recordedType: recorded ? recorded.type : "",
        count: recorded ? recorded.count : 0,
        candidates,
      };
    });
  }

  function yamlList(key, items) {
    return items.length
      ? [`${key}:`, ...items.map((item) => `  - ${item}`)]
      : [];
  }

  // Replay configuration of the runtime: scope, remap, accepted names and
  // internal outputs to record.
  function configYaml({ patterns, remap = {}, accept = [], outputs = [] }) {
    const pairs = Object.entries(remap).sort(([a], [b]) => a.localeCompare(b));
    return [
      ...yamlList("scope", patterns),
      ...(pairs.length
        ? ["remap:", ...pairs.map(([from, to]) => `  ${from}: ${to}`)]
        : []),
      ...yamlList("accept", [...accept].sort()),
      ...yamlList("outputs", [...outputs].sort()),
      "",
    ].join("\n");
  }

  function shellQuote(text) {
    return /^[\w@%+=:,./-]+$/.test(text)
      ? text
      : `'${text.replace(/'/g, "'\\''")}'`;
  }

  // Structure file of a mode, relative to the workspace of an isolated install.
  function structurePath(config, mode) {
    const pkg = config.packageName;
    return `install/${pkg}/share/${pkg}/exports/${config.deploymentName}/system_structure/${mode}.json`;
  }

  function command({ structure, bag, deploy, check }) {
    return [
      REPLAY_COMMAND,
      shellQuote(structure),
      `--config ${CONFIG_FILE}`,
      `--bag ${bag ? shellQuote(bag) : "<bag>"}`,
      ...(deploy ? [`--deploy ${shellQuote(deploy)}`] : []),
      ...(check ? ["--check"] : []),
    ].join(" ");
  }

  replay.model = {
    CONFIG_FILE,
    nodes,
    under,
    selectScope,
    patternsOf,
    boundary,
    parseBagMetadata,
    matchInputs,
    configYaml,
    structurePath,
    command,
  };
})();
