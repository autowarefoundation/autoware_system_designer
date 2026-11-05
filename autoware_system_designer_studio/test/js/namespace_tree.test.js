// Unit tests for the namespace tree built from a mode's system structure.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const WIDGET = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "widgets",
  "namespace_tree.js",
);

global.window = { SD: {} };
require(WIDGET);
const { NamespaceTree } = global.window.SD;

const structure = {
  name: "system",
  children: [
    {
      name: "perception",
      path: "/perception",
      unique_id: 1,
      entity_type: "module",
      children: [
        {
          name: "detector",
          path: "/perception/detector",
          unique_id: 2,
          entity_type: "node",
          children: [],
        },
        {
          name: "tracker",
          path: "/perception/tracker",
          unique_id: 3,
          entity_type: "node",
          children: [],
        },
      ],
    },
    { name: "planner", path: "/planning/planner", unique_id: 4, entity_type: "node", children: [] },
    { name: "unplaced", children: [] },
  ],
};

test("entities lists every child with a path and id, in design order, without the root", () => {
  const ids = NamespaceTree.entities(structure).map((e) => e.unique_id);
  assert.deepEqual(ids, [1, 2, 3, 4]);
  assert.deepEqual(NamespaceTree.entities(null), []);
});

test("build makes one entry per path segment and attaches entities at their path", () => {
  const root = NamespaceTree.build(NamespaceTree.entities(structure));
  assert.deepEqual([...root.children.keys()], ["perception", "planning"]);
  const perception = root.children.get("perception");
  assert.equal(perception.path, "/perception");
  assert.deepEqual(
    perception.entities.map((e) => e.unique_id),
    [1],
  );
  assert.deepEqual([...perception.children.keys()], ["detector", "tracker"]);
  const planning = root.children.get("planning");
  assert.deepEqual(planning.entities, []);
  assert.equal(planning.children.get("planner").entities[0].unique_id, 4);
});
