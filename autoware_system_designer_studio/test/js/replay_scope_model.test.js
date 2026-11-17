// Unit tests for the replay scope model: scope selection, boundary, bag match and configuration.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

global.window = {
  SD: { processTopics: ["/rosout", "/parameter_events", "/clock"] },
};
require(
  path.join(
    __dirname,
    "..",
    "..",
    "autoware_system_designer_studio",
    "web",
    "js",
    "activities",
    "replay",
    "scope_model.js",
  ),
);
const model = global.window.SD.activities.replay.model;

const STRING = "std_msgs/msg/String";

function port(topic, type = STRING, extra = {}) {
  return { name: topic, topic, msg_type: type, ...extra };
}

function node(nodePath, inPorts, outPorts, launcher = {}) {
  const segments = nodePath.split("/").filter(Boolean);
  return {
    entity_type: "node",
    name: segments[segments.length - 1],
    namespace: `/${segments.slice(0, -1).join("/")}`,
    path: nodePath,
    launcher: { launch_state: "single_node", ...launcher },
    in_ports: inPorts,
    out_ports: outPorts,
    children: [],
  };
}

// /src -> /in -> /mod/a -> /mid -> /mod/b -> /out -> /sink; /mod/b also hears /tf from /src.
function design() {
  return {
    entity_type: "system",
    children: [
      node("/src", [], [port("/in"), port("/tf")]),
      {
        entity_type: "module",
        path: "/mod",
        children: [
          node(
            "/mod/a",
            [port("/in"), port("/lonely")],
            [port("/mid"), port("/clock")],
            { launch_state: "composable_node", container_target: "/cc" },
          ),
          node(
            "/mod/b",
            [port("/mid"), port("/tf"), port("/srv/get", "pkg/srv/Get")],
            [port("/out")],
          ),
        ],
      },
      node("/sink", [port("/out")], [port("/srv/get", "pkg/srv/Get")]),
      node("/cc", [], [], { launch_state: "node_container" }),
    ],
  };
}

test("a scope path selects the nodes under it with their container", () => {
  const nodes = model.nodes(design());
  const scope = model.selectScope(nodes, ["/mod", "/nothing"]);

  assert.deepEqual(scope.nodes, ["/mod/a", "/mod/b"]);
  assert.deepEqual(scope.containers, ["/cc"]);
  assert.deepEqual(scope.unmatched, ["/nothing"]);
  assert.deepEqual(model.selectScope(nodes, ["/mo"]).nodes, []);
});

test("a bag path names the directory that holds its bag directory", () => {
  assert.equal(model.bagCollection("/data/bags/run"), "/data/bags");
  assert.equal(model.bagCollection("/data/bags/run/run_0.mcap"), "/data/bags");
  assert.equal(
    model.bagCollection("/data/bags/run/metadata.yaml"),
    "/data/bags",
  );
  assert.equal(model.bagCollection("/run"), "");
  assert.equal(model.bagCollection(""), "");
});

test("the fewest paths name a selection", () => {
  const nodes = model.nodes(design());
  const paths = (...selected) => model.patternsOf(nodes, new Set(selected));

  assert.deepEqual(paths("/mod/a", "/mod/b"), ["/mod"]);
  assert.deepEqual(paths("/mod/a", "/sink"), ["/mod/a", "/sink"]);
  assert.deepEqual(paths(...nodes.map((n) => n.path)), ["/"]);
  assert.deepEqual(paths(), []);
});

test("a node at a module's own path is selected by its key", () => {
  const root = design();
  const player = node("/mod/player", [], [port("/in")]);
  root.children.push({ ...player, path: "/mod", namespace: "/" });
  const nodes = model.nodes(root);
  const paths = (...selected) => model.patternsOf(nodes, new Set(selected));

  assert.equal(nodes[nodes.length - 1].key, "/mod/player");
  assert.deepEqual(model.selectScope(nodes, ["/mod"]).nodes, [
    "/mod/a",
    "/mod/b",
    "/mod",
  ]);
  assert.deepEqual(model.selectScope(nodes, ["/mod/player"]).nodes, ["/mod"]);
  assert.deepEqual(paths("/mod/a", "/mod/b", "/mod"), ["/mod"]);
  assert.deepEqual(paths("/mod/a", "/mod/b"), ["/mod/a", "/mod/b"]);
  assert.deepEqual(paths("/mod"), ["/mod/player"]);
});

test("the boundary holds outside inputs, outputs and service ports", () => {
  const nodes = model.nodes(design());
  const bound = model.boundary(nodes, ["/mod/a", "/mod/b"]);

  assert.deepEqual(
    bound.inputs.map((i) => [i.topic, i.publishers, i.subscribers]),
    [
      ["/in", ["/src"], ["/mod/a"]],
      ["/lonely", [], ["/mod/a"]],
      ["/tf", ["/src"], ["/mod/b"]],
    ],
  );
  assert.deepEqual(
    bound.outputs.map((o) => [o.topic, o.boundary]),
    [
      ["/mid", false],
      ["/out", true],
    ],
  );
  assert.deepEqual(bound.services, [
    {
      name: "/srv/get",
      type: "pkg/srv/Get",
      node: "/mod/b",
      role: "client",
      scopePeers: [],
    },
  ]);
});

test("bag metadata gives the recorded topics", () => {
  const bag = model.parseBagMetadata(
    [
      "rosbag2_bagfile_information:",
      "  version: 5",
      "  topics_with_message_count:",
      "    - topic_metadata:",
      "        name: /in",
      "        type: std_msgs/msg/String",
      '        offered_qos_profiles: "- history: 3\\n  depth: 0"',
      "      message_count: 3",
      "    - topic_metadata:",
      '        name: "/raw"',
      "        type: std_msgs/msg/String",
      "      message_count: 7",
      "  compression_format: ''",
      "  name: not_a_topic",
    ].join("\n"),
  );

  assert.deepEqual(
    [...bag],
    [
      ["/in", { type: STRING, count: 3 }],
      ["/raw", { type: STRING, count: 7 }],
    ],
  );
  assert.throws(() => model.parseBagMetadata("topics: []"), /metadata/);
});

test("inputs match the bag by name, remap and accepted names", () => {
  const nodes = model.nodes(design());
  const bound = model.boundary(nodes, ["/mod/a", "/mod/b"]);
  const bag = new Map([
    ["/raw", { type: STRING, count: 7 }],
    ["/tf", { type: "tf2_msgs/msg/TFMessage", count: 1 }],
  ]);
  const status = (remap, accept = []) =>
    Object.fromEntries(
      model
        .matchInputs(bound, bag, remap, new Set(accept))
        .map((i) => [i.topic, i.status]),
    );

  assert.deepEqual(status({}), {
    "/in": "missing",
    "/lonely": "unpublished",
    "/tf": "type",
  });
  assert.deepEqual(status({ "/raw": "/in" }, ["/lonely"]), {
    "/in": "remapped",
    "/lonely": "accepted",
    "/tf": "type",
  });
  const [input] = model.matchInputs(bound, bag, { "/raw": "/in" }, new Set());
  assert.equal(input.recordedName, "/raw");
  assert.deepEqual(input.candidates, ["/raw"]);
  assert.equal(
    model.matchInputs(bound, null, {}, new Set())[0].status,
    "unknown",
  );
});

test("the configuration and command name the scope, remap and bag", () => {
  assert.equal(
    model.configYaml({
      patterns: ["/mod"],
      remap: { "/raw": "/in" },
      accept: ["/srv/get", "/lonely"],
      outputs: ["/mid"],
      skip_outputs: ["/out"],
    }),
    [
      "scope:",
      "  - /mod",
      "remap:",
      "  /raw: /in",
      "accept:",
      "  - /lonely",
      "  - /srv/get",
      "outputs:",
      "  - /mid",
      "skip_outputs:",
      "  - /out",
      "",
    ].join("\n"),
  );
  const structure = model.structurePath(
    { packageName: "pkg", deploymentName: "Sys" },
    "default",
  );
  assert.equal(
    model.command({
      structure,
      bag: "/bags/my drive",
      deploy: "car",
      check: true,
    }),
    "autoware-system-designer-replay install/pkg/share/pkg/exports/Sys/system_structure/default.json" +
      " --config replay.yaml --bag '/bags/my drive' --deploy car --check",
  );
  assert.match(model.command({ structure }), /--bag <bag>$/);
});

global.window.SD.widgets = {};
require(
  path.join(
    __dirname,
    "..",
    "..",
    "autoware_system_designer_studio",
    "web",
    "js",
    "activities",
    "replay",
    "run_panel.js",
  ),
);
const { outcome, findings } = global.window.SD.activities.replay;

test("a replay job's outcome follows its pre-flight, result and verdict", () => {
  const job = (state, params, detail) => ({ state, params, detail });
  const done = { completed: true };
  const level = (...args) => outcome(job(...args)).level;

  assert.equal(level("running", {}, {}), "running");
  assert.equal(level("succeeded", { check: true }, { problems: [] }), "ok");
  assert.equal(
    level("failed", { check: true }, { problems: [{ level: "error" }] }),
    "failed",
  );
  assert.equal(
    level("succeeded", {}, { result: done, verdict: { passed: true } }),
    "ok",
  );
  assert.equal(
    level("failed", {}, { result: done, verdict: { passed: false } }),
    "failed",
  );
  assert.equal(level("succeeded", {}, { result: done }), "warning");
  assert.equal(
    level("succeeded", { save_reference: true }, { result: done }),
    "ok",
  );
  assert.match(
    outcome(job("failed", {}, { result: { error: "scope not up" } })).text,
    /scope not up/,
  );
  assert.equal(level("cancelled", {}, {}), "warning");
});

test("findings list problems and the topics that differ", () => {
  const lines = findings({
    detail: {
      problems: [{ level: "warning", message: "shadowed" }],
      verdict: {
        topics: [
          { topic: "/a", status: "match" },
          { topic: "/b", status: "differs", difference: { detail: "count" } },
        ],
      },
    },
  });

  assert.deepEqual(lines, [
    { level: "warning", text: "shadowed" },
    { level: "error", text: "/b: differs, count" },
  ]);
});

test("an input's choices are the bag topics of its type, unfed and accepted", () => {
  const nodes = model.nodes(design());
  const bound = model.boundary(nodes, ["/mod/a", "/mod/b"]);
  const bag = new Map([
    ["/raw", { type: STRING, count: 7 }],
    ["/tf", { type: "tf2_msgs/msg/TFMessage", count: 1 }],
  ]);
  const choices = (remap, accept, withBag = bag) =>
    Object.fromEntries(
      model
        .matchInputs(bound, withBag, remap, new Set(accept))
        .map((input) => [
          input.topic,
          model
            .inputChoices(input, withBag)
            .map((c) => (c.current ? `[${c.value}]` : c.value)),
        ]),
    );

  assert.deepEqual(choices({ "/raw": "/in" }, ["/lonely"]), {
    "/in": ["[/raw]", "", model.ACCEPT],
    "/lonely": ["/raw", "", `[${model.ACCEPT}]`],
    "/tf": ["/raw", "[/tf]"],
  });
  assert.deepEqual(choices({}, [], null)["/in"], ["[]", model.ACCEPT]);
});

test("the graph joins bag topics, boundary ports and the scope's node ports", () => {
  const nodes = model.nodes(design());
  const scope = model.selectScope(nodes, ["/mod"]);
  const bound = model.boundary(nodes, scope.nodes);
  const bag = new Map([["/raw", { type: STRING, count: 7 }]]);
  const matched = model.matchInputs(bound, bag, { "/raw": "/in" }, new Set());
  const draw = (recorded, skipped = []) => {
    const graph = model.graph(
      nodes,
      scope,
      bound,
      matched,
      new Set(recorded),
      new Set(skipped),
    );
    const names = new Map();
    graph.bag.forEach((row) => names.set(row.id, `bag ${row.name}`));
    graph.inputs.forEach((input) => names.set(input.id, `in ${input.topic}`));
    graph.nodes.forEach((item) =>
      [...item.inputs, ...item.outputs].forEach((p) =>
        names.set(p.id, `${item.path}:${p.name}`),
      ),
    );
    return {
      graph,
      edges: graph.edges.map(
        (e) => `${e.kind} ${names.get(e.from)} > ${names.get(e.to)}`,
      ),
    };
  };

  const { graph, edges } = draw([]);
  assert.deepEqual(
    graph.nodes.map((item) => [item.path, item.name, item.container]),
    [
      ["/mod/a", "a", false],
      ["/mod/b", "b", false],
      ["/cc", "cc", true],
    ],
  );
  assert.deepEqual(
    graph.bag.map((row) => [row.name, row.input, row.status]),
    [["/raw", "/in", "remapped"]],
  );
  assert.deepEqual(edges, [
    "feed bag /raw > in /in",
    "input in /in > /mod/a:/in",
    "input in /lonely > /mod/a:/lonely",
    "internal /mod/a:/mid > /mod/b:/mid",
    "input in /tf > /mod/b:/tf",
  ]);
  const nested = (items) =>
    items.map((item) =>
      item.node
        ? graph.nodes.find((n) => n.id === item.node).name
        : [item.label, item.depth, nested(item.items)],
    );
  assert.deepEqual(nested(graph.boxes), ["cc", ["mod", 0, ["a", "b"]]]);
  assert.equal(graph.levels, 1);
  const marks = (drawn) =>
    drawn.nodes
      .flatMap((item) => item.outputs)
      .map((p) => [p.topic, p.boundary, p.recorded]);
  assert.deepEqual(marks(graph), [
    ["/mid", false, false],
    ["/out", true, true],
  ]);
  const chosen = draw(["/mid"], ["/out"]).graph;
  assert.deepEqual(marks(chosen), [
    ["/mid", false, true],
    ["/out", true, false],
  ]);
  assert.equal(chosen.key, graph.key);
});
