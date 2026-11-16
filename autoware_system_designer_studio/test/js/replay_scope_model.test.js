// Unit tests for the replay scope model: scope selection, boundary, bag match and configuration.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

global.window = { SD: {} };
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

test("the fewest paths name a selection", () => {
  const nodes = model.nodes(design());
  const paths = (...selected) => model.patternsOf(nodes, new Set(selected));

  assert.deepEqual(paths("/mod/a", "/mod/b"), ["/mod"]);
  assert.deepEqual(paths("/mod/a", "/sink"), ["/mod/a", "/sink"]);
  assert.deepEqual(paths(...nodes.map((n) => n.path)), ["/"]);
  assert.deepEqual(paths(), []);
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
