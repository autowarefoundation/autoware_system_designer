// Unit tests for the Topics view's model: design topics with their declared
// rates, observed rates of the running units, row status and grouping.

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
  node("/planning/planner", { ins: [port("/perception/objects", "x.in")] }),
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

test("rows group by component, node namespace or topic namespace, problems first", () => {
  const design = launch.designTopics(ENTITIES);
  const rows = launch.topicRows(design, new Map());
  const labels = (method) =>
    launch.groupRows(rows, method).map(([label]) => label);
  assert.deepEqual(labels("component"), [
    "/control",
    "/map",
    "/perception",
    "/sensing",
  ]);
  assert.deepEqual(labels("namespace"), ["/", "/perception", "/sensing/lidar"]);
  assert.deepEqual(labels("topic"), [
    "/control",
    "/map",
    "/perception",
    "/sensing/lidar",
  ]);
  const deep = {
    ...rows[0],
    topic: "/perception/object_recognition/detection/objects",
  };
  assert.deepEqual(
    launch.groupRows([deep], "topic").map(([label]) => label),
    ["/perception/object_recognition"],
  );
  assert.deepEqual(labels("none"), [""]);

  const ok = { ...rows[0], topic: "/a", status: "ok" };
  const silent = { ...rows[0], topic: "/b", status: "silent" };
  const [[, ordered]] = launch.groupRows([ok, silent], "none");
  assert.deepEqual(
    ordered.map((row) => row.topic),
    ["/b", "/a"],
  );
});
