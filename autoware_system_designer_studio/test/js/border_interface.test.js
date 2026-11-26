// Unit tests for the border interface sheet: the rows joining the declared
// interface ports with the structure's boundary ports, on a stub window.

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

let Module = null;
global.DiagramBase = class {};
global.window = {
  SD: {
    diagrams: { register: (_type, ModuleClass) => (Module = ModuleClass) },
  },
};
require(path.join(JS_DIR, "diagrams", "border_interface.js"));

const port = (id, name, portPath, msgType = "") => ({
  unique_id: id,
  name,
  port_path: portPath,
  msg_type: msgType,
});

test("port rows carry the boundary port and the instance ports linked to it", () => {
  const cloud = port("b1", "pointcloud", "/input/pointcloud", "pkg/msg/Cloud");
  const objects = port("b2", "objects", "/output/objects", "pkg/msg/Objects");
  const links = [
    {
      from_port: cloud,
      to_port: port("c1", "pointcloud", "/a/input/pointcloud"),
    },
    { from_port: cloud, to_port: port("c2", "cloud", "/b/input/cloud") },
    { from_port: port("c3", "objects", "/a/output/objects"), to_port: objects },
  ];

  const inputs = Module.portRows(
    "input",
    [{ name: "pointcloud", topic: "/sensing/pointcloud" }],
    [cloud],
    links,
  );
  assert.deepEqual(inputs, [
    {
      name: "pointcloud",
      topic: "/sensing/pointcloud",
      data: cloud,
      msgType: "pkg/msg/Cloud",
      peers: ["/a/input/pointcloud", "/b/input/cloud"],
    },
  ]);

  const outputs = Module.portRows(
    "output",
    [{ name: "objects", topic: "/perception/objects" }],
    [objects],
    links,
  );
  assert.deepEqual(outputs[0].peers, ["/a/output/objects"]);
});

test("a declared port without a boundary port keeps its topic", () => {
  const rows = Module.portRows(
    "input",
    [{ name: "map", topic: "/map/vector_map" }],
    [],
    [],
  );
  assert.deepEqual(rows, [
    {
      name: "map",
      topic: "/map/vector_map",
      data: null,
      msgType: "",
      peers: [],
    },
  ]);
});
