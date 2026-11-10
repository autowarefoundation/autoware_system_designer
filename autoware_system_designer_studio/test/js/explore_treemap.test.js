// Unit tests for the treemap's layout rules: slot and row placement of the
// top-level components, sections per compute unit, proportional lengths,
// squarified cells and the nested box layout, on a stub window.

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

const registry = new Map();
global.DiagramBase = class {};
global.window = {
  SD: {
    diagrams: {
      register: (type, cls) => registry.set(type, cls),
    },
  },
};
require(path.join(JS_DIR, "diagrams", "treemap.js"));
const Treemap = registry.get("treemap");
const {
  slotOf,
  nodeCount,
  unitSections,
  slotRows,
  proportional,
  squarify,
  layout,
  GEOMETRY,
  CONTAINERS,
} = Treemap;

let nextId = 1;
function node(name, namespace, unit, launchState = "single_node") {
  return {
    entity_type: "node",
    name,
    namespace,
    path: `${namespace}/${name}`,
    unique_id: nextId++,
    compute_unit: unit,
    launcher: { launch_state: launchState },
  };
}
function module(name, pathValue, unit, children) {
  return {
    entity_type: "module",
    name,
    path: pathValue,
    unique_id: nextId++,
    compute_unit: unit,
    children,
  };
}

function structure() {
  return {
    entity_type: "system",
    unique_id: 0,
    compute_units: [
      { name: "main_ecu", kind: "onboard" },
      { name: "perception_ecu", kind: "onboard" },
      { name: "idle_ecu", kind: "onboard" },
    ],
    children: [
      module("sensing", "/sensing", "main_ecu", [
        module("lidar", "/sensing/lidar", "main_ecu", [
          node("driver", "/sensing/lidar", "main_ecu"),
          node("filter", "/sensing/lidar", "perception_ecu"),
        ]),
        node("camera", "/sensing", "main_ecu"),
      ]),
      module("object_recognition", "/perception/object_recognition", "perception_ecu", [
        node("detector", "/perception/object_recognition", "perception_ecu"),
        node("tracker", "/perception/object_recognition", "perception_ecu"),
      ]),
      node("control", "/control", "main_ecu"),
      node("vehicle", "/vehicle", "main_ecu"),
      node("map", "/map", "extra_ecu"),
      node("pointcloud_container", "/pointcloud_container", "main_ecu", "node_container"),
      module("dummy", "/dummy", "main_ecu", []),
      node("orphan", "/orphan", undefined),
    ],
  };
}

test("slot of a component is its first path segment, containers aside", () => {
  assert.equal(slotOf(module("object_recognition", "/perception/object_recognition")), "perception");
  assert.equal(slotOf(node("control", "/control")), "control");
  assert.equal(slotOf(node("pc", "/pc", "u", "node_container")), CONTAINERS);
  assert.equal(slotOf({ entity_type: "module", name: "bare", path: "" }), "bare");
});

test("sections follow declaration order, name undeclared units, leave empty ones out", () => {
  const sections = unitSections(structure());
  assert.deepEqual(
    sections.map((s) => [s.name, s.kind, s.nodes]),
    [
      ["main_ecu", "onboard", 5],
      ["perception_ecu", "onboard", 3],
      ["extra_ecu", "undeclared", 1],
      ["", "", 1],
    ],
  );
  const main = sections[0];
  assert.deepEqual(
    main.children.map((c) => c.name),
    ["sensing", "control", "vehicle", "pointcloud_container", "dummy"],
  );
  // A module split across units keeps only the unit's nodes in each section.
  const lidar = main.children[0].children[0];
  assert.deepEqual(lidar.children.map((c) => c.name), ["driver"]);
  const perception = sections[1];
  assert.deepEqual(perception.children.map((c) => c.name), ["sensing", "object_recognition"]);
  assert.deepEqual(
    perception.children[0].children[0].children.map((c) => c.name),
    ["filter"],
  );
  // An empty module stays on its own unit as a one-weight block.
  assert.equal(nodeCount(main.children[4]), 0);
});

test("rows keep their fixed slot order and put other segments after the containers", () => {
  const main = unitSections(structure())[0];
  const rows = slotRows(main.children);
  assert.deepEqual(
    rows.map((row) => row.map((slot) => slot.slot)),
    [["sensing"], ["control"], [CONTAINERS, "vehicle", "dummy"]],
  );
  assert.equal(rows[0][0].weight, 2);
  const perception = unitSections(structure())[1];
  assert.deepEqual(
    slotRows(perception.children).map((row) => row.map((slot) => slot.slot)),
    [["sensing", "perception"]],
  );
});

test("proportional lengths honour the minimum and fall back when it does not fit", () => {
  assert.deepEqual(proportional([1, 3], 100, 10), [30, 70]);
  assert.deepEqual(proportional([1, 3], 100, 0), [25, 75]);
  assert.deepEqual(proportional([1, 1, 1], 30, 20), [10, 10, 10]);
  assert.deepEqual(proportional([1, 1, 1], 30, 20, true), [20, 20, 20]);
  assert.deepEqual(proportional([0, 0], 50, 0), [25, 25]);
  assert.deepEqual(proportional([], 50, 0), []);
});

test("squarified cells tile the rect with areas in proportion to the weights", () => {
  const rect = { x: 10, y: 20, w: 200, h: 100 };
  const items = [{ weight: 6 }, { weight: 1 }, { weight: 2 }, { weight: 1 }];
  const cells = squarify(items, rect);
  assert.equal(cells.length, 4);
  const area = (c) => c.w * c.h;
  const total = cells.reduce((sum, c) => sum + area(c), 0);
  assert.ok(Math.abs(total - rect.w * rect.h) < 1e-6);
  cells.forEach((cell) => {
    const expected = (cell.item.weight / 10) * rect.w * rect.h;
    assert.ok(Math.abs(area(cell) - expected) < 1e-6);
    assert.ok(cell.x >= rect.x - 1e-9 && cell.x + cell.w <= rect.x + rect.w + 1e-9);
    assert.ok(cell.y >= rect.y - 1e-9 && cell.y + cell.h <= rect.y + rect.h + 1e-9);
  });
  assert.deepEqual(squarify([], rect), []);
  assert.deepEqual(squarify(items, { x: 0, y: 0, w: 0, h: 10 }), []);
});

function within(inner, outer) {
  const eps = 1e-6;
  return (
    inner.x >= outer.x - eps &&
    inner.y >= outer.y - eps &&
    inner.x + inner.w <= outer.x + outer.w + eps &&
    inner.y + inner.h <= outer.y + outer.h + eps
  );
}

test("layout stacks the unit sections and nests every block in its slot", () => {
  const sections = unitSections(structure());
  const { height, boxes } = layout(sections, 800, 2000);
  const units = boxes.filter((b) => b.kind === "unit");
  assert.equal(units.length, 4);
  assert.equal(height, 2000);
  units.forEach((unit) => assert.ok(unit.h >= GEOMETRY.minUnit));
  assert.equal(units.reduce((sum, u) => sum + u.h, 0), height);
  let y = 0;
  units.forEach((unit) => {
    assert.equal(unit.y, y);
    y += unit.h;
  });
  const slots = boxes.filter((b) => b.kind === "slot");
  const blocks = boxes.filter((b) => b.kind === "module" || b.kind === "node");
  blocks.forEach((block) => {
    const slot = slots.find((s) => s.section === block.section && within(block, s));
    assert.ok(slot, `${block.instance.name} lies in a slot`);
    assert.ok(within(block, units[block.section]));
  });
  const nodes = blocks.filter((b) => b.kind === "node").map((b) => b.instance.name);
  assert.equal(nodes.length, 10);
  const byId = new Map(blocks.map((b) => [`${b.section}:${b.id}`, b]));
  // A child lies inside its open parent.
  const lidar = blocks.find((b) => b.instance.name === "lidar" && b.section === 0);
  assert.ok(lidar.open);
  const driver = blocks.find((b) => b.instance.name === "driver");
  assert.ok(within(driver, lidar));
  assert.ok(byId.has("0:" + lidar.id));
});

test("a short canvas keeps every section at its minimum height and closes small modules", () => {
  const sections = unitSections(structure());
  const { height, boxes } = layout(sections, 800, 300);
  const units = boxes.filter((b) => b.kind === "unit");
  assert.equal(height, units.length * GEOMETRY.minUnit);
  const blocks = boxes.filter((b) => b.kind === "module" || b.kind === "node");
  const sensing = blocks.find((b) => b.instance.name === "sensing" && b.section === 0);
  assert.ok(sensing && !sensing.open);
  assert.ok(!blocks.some((b) => b.instance.name === "driver"));
});

test("the depth limit closes the blocks at that level", () => {
  const sections = unitSections(structure());
  const { boxes } = layout(sections, 800, 600, 1);
  const blocks = boxes.filter((b) => b.kind === "module" || b.kind === "node");
  assert.ok(blocks.every((b) => b.depth === 1 && !b.open));
  assert.ok(!blocks.some((b) => b.instance.name === "lidar"));
  const two = layout(sections, 800, 600, 2).boxes.filter((b) => b.kind === "module");
  assert.ok(two.some((b) => b.instance.name === "lidar" && !b.open));
  assert.ok(two.some((b) => b.instance.name === "sensing" && b.open));
});

test("a taller unit head lowers the slots of every section by the difference", () => {
  const sections = unitSections(structure());
  const head = GEOMETRY.unitHead + 10;
  const slotTops = (boxes) => {
    const units = boxes.filter((b) => b.kind === "unit");
    return units.map((unit, index) =>
      Math.min(...boxes.filter((b) => b.kind === "slot" && b.section === index).map((b) => b.y - unit.y)),
    );
  };
  const base = layout(sections, 800, 2000);
  const tall = layout(sections, 800, 2000, Infinity, head);
  assert.equal(tall.height, base.height);
  slotTops(base.boxes).forEach((top) => assert.equal(top, GEOMETRY.unitHead));
  slotTops(tall.boxes).forEach((top) => assert.equal(top, head));
});
