// Unit tests for the Topics view's model: design topics with their declared
// rates, observed rates of the running units, row status and grouping, the
// node rows and the ends of a topic.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const JS_DIR = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
);

global.window = { SD: { activities: {} } };
require(path.join(JS_DIR, "activities", "launch", "topic_rates.js"));
const launch = global.window.SD.activities.launch;

function port(topic, eventId, triggers = [], rates = {}) {
  return {
    name: topic,
    msg_type: "std_msgs/msg/String",
    topic: topic.split("/").filter(Boolean),
    event: {
      unique_id: eventId,
      trigger_ids: triggers,
      action_ids: [],
      ...rates,
    },
  };
}

function node(
  nodePath,
  { outs = [], ins = [], events = [], intra = false } = {},
) {
  return {
    entity_type: "node",
    name: nodePath.split("/").pop(),
    path: nodePath,
    member_path: nodePath,
    launcher: { use_intra_process_comms: intra },
    out_ports: outs,
    in_ports: ins,
    events,
  };
}

const ENTITIES = [
  node("/sensing/lidar/driver", {
    outs: [port("/sensing/lidar/points", "d.out", ["d.tick"])],
    events: [{ unique_id: "d.tick", type: "periodic", frequency: 10 }],
  }),
  node("/perception/detector", {
    ins: [port("/sensing/lidar/points", "p.in")],
    outs: [port("/perception/objects", "p.out", ["p.run"])],
    events: [{ unique_id: "p.run", type: "on_input" }],
  }),
  node("/map", {
    outs: [port("/map/vector_map", "m.out", ["m.once"], { frequency: 0 })],
    events: [{ unique_id: "m.once", type: "once" }],
  }),
  node("/control", {
    outs: [
      port("/control/cmd", "c.out", ["c.run"], {
        frequency: 10,
        warn_rate: 3.3,
        error_rate: 2,
      }),
    ],
    events: [{ unique_id: "c.run", type: "on_input" }],
  }),
  node("/perception/filter", {
    outs: [port("/perception/filtered", "f.out", ["f.tick"])],
    events: [{ unique_id: "f.tick", type: "periodic", frequency: 10 }],
    intra: true,
  }),
  node("/planning/planner", {
    ins: [port("/perception/objects", "x.in"), port("", "x.unconnected")],
  }),
];

function snapshot(nodes) {
  return { interval_s: 2, nodes };
}

test("design topics carry their publishers, subscribers and declared rate", () => {
  const design = launch.designTopics(ENTITIES);
  const points = design.get("/sensing/lidar/points");
  assert.deepEqual(points.publishers, [
    {
      path: "/sensing/lidar/driver",
      declaredHz: 10,
      warnHz: null,
      errorHz: null,
      once: false,
      intra: false,
    },
  ]);
  // The port's own event declares a propagated rate and the system monitor's thresholds.
  const cmd = design.get("/control/cmd");
  assert.deepEqual([cmd.declaredHz, cmd.warnHz, cmd.errorHz], [10, 3.3, 2]);
  assert.equal(design.get("/map/vector_map").once, true);
  assert.deepEqual(points.subscribers, ["/perception/detector"]);
  assert.equal(points.declaredHz, 10);
  assert.equal(design.get("/perception/objects").declaredHz, null);
  // A port connected to no topic is no interface.
  assert.equal(design.has("/"), false);
});

test("rows compare the observed rate with the declared one", () => {
  const design = launch.designTopics(ENTITIES);
  const observed = launch.observedTopics(
    new Map([
      [
        "main_ecu",
        snapshot([
          {
            node: "/sensing/lidar/driver",
            outputs: [
              { topic: "/sensing/lidar/points", hz: 6, callback_ms: 1.5 },
            ],
            inputs: [],
          },
          {
            node: "/perception/detector",
            inputs: [{ topic: "/sensing/lidar/points", hz: 6, comm_ms: 0.4 }],
            outputs: [{ topic: "/perception/objects", hz: 0, silent_s: 4 }],
          },
          {
            node: "/perception/filter",
            outputs: [{ topic: "/perception/filtered", hz: 0 }],
            inputs: [],
          },
          {
            node: "/control",
            outputs: [{ topic: "/control/cmd", hz: 3 }],
            inputs: [],
          },
          {
            node: "/map",
            outputs: [{ topic: "/map/vector_map", hz: 0 }],
            inputs: [],
          },
          {
            node: "/debug",
            outputs: [
              { topic: "/debug/markers", hz: 2 },
              { topic: "/rosout", hz: 1 },
            ],
            inputs: [],
          },
        ]),
      ],
    ]),
  );
  const byTopic = (rows) => new Map(rows.map((row) => [row.topic, row]));
  const rows = byTopic(launch.topicRows(design, observed));
  const points = rows.get("/sensing/lidar/points");
  assert.equal(points.status, "slow");
  assert.equal(points.hz, 6);
  assert.equal(points.callbackMs, 1.5);
  assert.equal(points.commMs, 0.4);
  assert.equal(rows.get("/perception/objects").status, "silent");
  assert.equal(rows.get("/perception/objects").silentS, 4);
  // Intra-process publishes never reach rcl, so a zero rate is not a silence.
  assert.equal(rows.get("/perception/filtered").status, "intra");
  // Declared thresholds: 3 Hz is above the error rate, below the warn rate.
  assert.equal(rows.get("/control/cmd").status, "slow");
  assert.equal(rows.get("/map/vector_map").status, "once");
  assert.equal(rows.has("/debug/markers"), false);

  const all = byTopic(launch.topicRows(design, observed, { undeclared: true }));
  assert.equal(all.get("/debug/markers").status, "ok");
  assert.equal(all.get("/debug/markers").declared, false);
  assert.equal(all.has("/rosout"), false);
});

test("a topic without counted publishes takes its subscribers' rate; unseen and new are told apart", () => {
  const design = launch.designTopics(ENTITIES);
  const observed = launch.observedTopics(
    new Map([
      [
        "perception_ecu",
        snapshot([
          {
            node: "/perception/detector",
            inputs: [{ topic: "/sensing/lidar/points", hz: 10, comm_ms: 2 }],
            outputs: [{ topic: "/perception/objects", hz: null }],
          },
        ]),
      ],
    ]),
  );
  const rows = new Map(
    launch.topicRows(design, observed).map((r) => [r.topic, r]),
  );
  const points = rows.get("/sensing/lidar/points");
  assert.equal(points.hz, 10);
  assert.equal(points.rateFrom, "take");
  assert.equal(points.status, "ok");
  assert.equal(rows.get("/perception/objects").status, "new");
  assert.equal(rows.get("/perception/filtered").status, "unseen");

  // Every node of a topic on a unit that is not running: offline, not unseen.
  const entities = ENTITIES.map((entity) => ({
    ...entity,
    compute_unit: entity.path.startsWith("/perception")
      ? "perception_ecu"
      : "main_ecu",
  }));
  const units = launch.topicRows(launch.designTopics(entities), new Map(), {
    runningUnits: new Set(["perception_ecu"]),
  });
  const status = new Map(units.map((r) => [r.topic, r.status]));
  assert.equal(status.get("/perception/filtered"), "unseen");
  assert.equal(status.get("/control/cmd"), "offline");
  assert.equal(status.get("/sensing/lidar/points"), "unseen"); // its subscriber runs
});

// Labels of a group tree, nested as [label, [sub-groups...], [topics...]].
function shape(node) {
  return [
    node.label,
    node.children.map(shape),
    node.rows.map((row) => row.topic),
  ];
}

test("rows sit in a namespace tree; single-child chains merge", () => {
  const design = launch.designTopics(ENTITIES);
  const rows = launch.topicRows(design, new Map());
  const tree = launch.groupTree(rows, launch.topicGroup);
  assert.deepEqual(shape(tree), [
    "",
    [
      ["/control", [], ["/control/cmd"]],
      ["/map", [], ["/map/vector_map"]],
      ["/perception", [], ["/perception/filtered", "/perception/objects"]],
      ["/sensing/lidar", [], ["/sensing/lidar/points"]],
    ],
    [],
  ]);
  assert.equal(tree.count, rows.length);
  assert.equal(tree.children[2].statuses.get("unseen"), 2);

  const topics = [
    "/perception/object_recognition/detection/objects",
    "/perception/object_recognition/tracking/objects",
    "/perception/object_recognition/objects",
    "/perception/traffic_light/signals",
  ].map((topic) => ({ ...rows[0], topic }));
  assert.deepEqual(shape(launch.groupTree(topics, launch.topicGroup)), [
    "",
    [
      [
        "/perception",
        [
          [
            "/object_recognition",
            [
              [
                "/detection",
                [],
                ["/perception/object_recognition/detection/objects"],
              ],
              [
                "/tracking",
                [],
                ["/perception/object_recognition/tracking/objects"],
              ],
            ],
            ["/perception/object_recognition/objects"],
          ],
          ["/traffic_light", [], ["/perception/traffic_light/signals"]],
        ],
        [],
      ],
    ],
    [],
  ]);
  const flat = launch.groupTree(rows);
  assert.equal(flat.children.length, 0);
  assert.equal(flat.rows.length, rows.length);
});

const OBSERVED = new Map([
  [
    "main_ecu",
    snapshot([
      {
        node: "/sensing/lidar/driver",
        outputs: [{ topic: "/sensing/lidar/points", hz: 6, callback_ms: 1.5 }],
        inputs: [],
      },
      {
        node: "/perception/detector",
        inputs: [{ topic: "/sensing/lidar/points", hz: 6, comm_ms: 0.4 }],
        outputs: [{ topic: "/perception/objects", hz: 0, silent_s: 4 }],
      },
      {
        node: "/rviz",
        inputs: [{ topic: "/perception/objects", hz: 0 }],
        outputs: [{ topic: "/debug/markers", hz: 2 }],
      },
    ]),
  ],
]);

test("node rows carry the interfaces of each node with its own rates", () => {
  const design = launch.designTopics(ENTITIES);
  const rows = launch.topicRows(design, launch.observedTopics(OBSERVED));
  const nodes = new Map(
    launch.nodeRows(ENTITIES, rows).map((row) => [row.node, row]),
  );
  assert.equal(nodes.size, ENTITIES.length);
  const detector = nodes.get("/perception/detector");
  assert.deepEqual(
    detector.inputs.map((f) => [f.port, f.topic, f.seen, f.hz, f.ms]),
    [["/sensing/lidar/points", "/sensing/lidar/points", true, 6, 0.4]],
  );
  assert.equal(detector.outputs[0].row.status, "silent");
  // The worst output decides; a node without outputs takes its inputs' status.
  assert.equal(detector.status, "silent");
  assert.equal(nodes.get("/sensing/lidar/driver").status, "slow");
  const planner = nodes.get("/planning/planner");
  assert.equal(planner.status, "silent");
  // A port connected to no topic stays an interface without a row.
  assert.deepEqual(
    planner.inputs.map((f) => [f.topic, f.seen]),
    [
      ["/perception/objects", false],
      [null, false],
    ],
  );
  assert.equal(nodes.has("/rviz"), false);

  // Undeclared: nodes seen outside the design, with their interfaces off the ports.
  const all = launch.topicRows(design, launch.observedTopics(OBSERVED), {
    undeclared: true,
  });
  const rviz = launch
    .nodeRows(ENTITIES, all, { undeclared: true })
    .find((row) => row.node === "/rviz");
  assert.equal(rviz.declared, false);
  assert.equal(rviz.unit, "main_ecu");
  assert.deepEqual(
    rviz.inputs.map((f) => [f.port, f.topic]),
    [[null, "/perception/objects"]],
  );
  assert.deepEqual(
    rviz.outputs.map((f) => f.topic),
    ["/debug/markers"],
  );

  const tree = launch.groupTree(
    [...nodes.values()],
    launch.nodeGroup,
    launch.compareNodes("node", 1),
  );
  assert.deepEqual(
    tree.children.map((group) => group.label),
    ["/perception", "/planning", "/sensing/lidar"],
  );
  assert.deepEqual(
    tree.rows.map((row) => row.node),
    ["/control", "/map"],
  );
  assert.deepEqual(
    tree.children[0].rows.map((row) => row.node),
    ["/perception/detector", "/perception/filter"],
  );
});

test("a topic's ends are its declared and seen nodes", () => {
  const design = launch.designTopics(ENTITIES);
  const rows = launch.topicRows(design, launch.observedTopics(OBSERVED));
  const objects = rows.find((row) => row.topic === "/perception/objects");
  const ends = launch.topicEnds(objects, () => "main_ecu");
  assert.deepEqual(ends.sources, [
    {
      node: "/perception/detector",
      declared: true,
      unit: "main_ecu",
      seen: true,
      hz: 0,
      ms: null,
    },
  ]);
  assert.deepEqual(
    ends.consumers.map((e) => [e.node, e.declared, e.seen, e.unit]),
    [
      ["/planning/planner", true, false, "main_ecu"],
      ["/rviz", false, true, "main_ecu"],
    ],
  );
});

test("rows sort by a column within their group, missing values last", () => {
  const base = launch.topicRows(launch.designTopics(ENTITIES), new Map())[0];
  const row = (topic, status, hz) => ({ ...base, topic, status, hz });
  const rows = [
    row("/a", "ok", 5),
    row("/b", "silent", 0),
    row("/c", "ok", null),
    row("/d", "slow", 20),
  ];
  const order = (key, dir) =>
    launch
      .groupTree(rows, undefined, launch.compareRows(key, dir))
      .rows.map((r) => r.topic);
  assert.deepEqual(order("status", 1), ["/b", "/d", "/a", "/c"]);
  assert.deepEqual(order("hz", 1), ["/b", "/a", "/d", "/c"]);
  assert.deepEqual(order("hz", -1), ["/d", "/a", "/b", "/c"]);
  assert.deepEqual(order("topic", -1), ["/d", "/c", "/b", "/a"]);
});

test("a selected topic marks its publishers' interfaces by direction", () => {
  const design = launch.designTopics(ENTITIES);
  const rows = launch.topicRows(design, new Map());
  const byTopic = new Map(rows.map((r) => [r.topic, r]));
  const objects = byTopic.get("/perception/objects");
  const nodes = launch.topicNodes(objects);
  assert.deepEqual([...nodes], ["/perception/detector"]);
  const marks = launch.flowMarks(rows, nodes);
  assert.equal(marks.get("/perception/objects"), "out");
  assert.equal(marks.get("/sensing/lidar/points"), "in");
  assert.equal(marks.has("/control/cmd"), false);
  // A topic without a publisher marks its subscribers' interfaces.
  const orphan = {
    ...objects,
    topic: "/orphan",
    designPublishers: [],
    publishers: [],
    designSubscribers: ["/planning/planner"],
  };
  assert.deepEqual([...launch.topicNodes(orphan)], ["/planning/planner"]);
  assert.equal(
    launch.flowMarks([orphan], new Set(["/planning/planner"])).get("/orphan"),
    "in",
  );
  assert.equal(launch.flowMarks(rows, new Set()).size, 0);
});

test("services are told from topics by the port role, else by the type", () => {
  const srv = (name, role) => ({
    name,
    msg_type: "std_srvs/srv/Trigger",
    topic: name.split("/").filter(Boolean),
    event: { unique_id: `${name}.${role}`, trigger_ids: [], action_ids: [] },
    ...(role ? { role } : {}),
  });
  const entities = [
    node("/map/loader", {
      outs: [srv("/map/get_map", "server"), port("/map/vector_map", "l.out")],
    }),
    node("/planning/planner", {
      ins: [srv("/map/get_map", "client"), port("/map/vector_map", "p.in")],
    }),
    // An export without roles: the type tells.
    node("/old", { outs: [srv("/old/reset")] }),
  ];
  assert.equal(
    launch.isService({ msg_type: "a/srv/B", role: "publisher" }),
    false,
  );
  const rows = launch.topicRows(launch.designTopics(entities), new Map());
  const byTopic = new Map(rows.map((row) => [row.topic, row]));
  const getMap = byTopic.get("/map/get_map");
  assert.deepEqual(
    [
      getMap.service,
      getMap.status,
      getMap.designPublishers,
      getMap.designSubscribers,
    ],
    [true, "service", ["/map/loader"], ["/planning/planner"]],
  );
  assert.equal(byTopic.get("/old/reset").service, true);
  assert.equal(byTopic.get("/map/vector_map").service, false);

  // The services sit in their own group, after the namespaces.
  const tree = launch.groupTree(rows, launch.topicGroup);
  assert.deepEqual(
    tree.children.map((group) => group.label),
    ["/map", "Services"],
  );
  assert.deepEqual(
    tree.children[1].children.map((group) => [
      group.label,
      group.rows.map((row) => row.topic),
    ]),
    [
      ["/map", ["/map/get_map"]],
      ["/old", ["/old/reset"]],
    ],
  );

  // A node's topics come ahead of its services; a service decides no status.
  const loader = launch
    .nodeRows(entities, rows)
    .find((row) => row.node === "/map/loader");
  assert.deepEqual(
    loader.outputs.map((face) => [face.topic, face.service]),
    [
      ["/map/vector_map", false],
      ["/map/get_map", true],
    ],
  );
  assert.equal(loader.status, "unseen");
});
