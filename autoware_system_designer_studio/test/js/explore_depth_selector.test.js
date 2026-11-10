// Unit tests for the Explore depth switch: the levels of a structure and the
// level the store's value names, on a stub window.

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

global.window = {
  SD: {
    activities: {},
    widgets: {
      element: () => ({}),
      section: () => ({}),
      segmented: () => ({}),
    },
  },
};
require(path.join(JS_DIR, "activities", "explore", "depth_selector.js"));
const { structureDepth, effectiveDepth, switchValue, switchOptions } =
  global.window.SD.activities.explore.depthSelector;

test("structure depth counts the levels below the root", () => {
  const root = {
    children: [{ children: [{ children: [{}] }, {}] }, {}],
  };
  assert.equal(structureDepth(root), 3);
  assert.equal(structureDepth({ children: [{}] }), 1);
  assert.equal(structureDepth({}), 0);
  assert.equal(structureDepth(null), 0);
});

test("an empty or deeper choice names the deepest level", () => {
  assert.equal(effectiveDepth("", 3), 3);
  assert.equal(effectiveDepth(undefined, 3), 3);
  assert.equal(effectiveDepth("5", 3), 3);
  assert.equal(effectiveDepth("2", 3), 2);
  assert.equal(effectiveDepth("0", 3), 3);
});

test("the switch marks a level above the deepest, otherwise N", () => {
  assert.equal(switchValue("2", 3), "2");
  assert.equal(switchValue("3", 3), "");
  assert.equal(switchValue("5", 3), "");
  assert.equal(switchValue("", 3), "");
  assert.equal(switchValue("0", 3), "");
});

test("the switch offers up to five levels, ending with N when deeper", () => {
  assert.deepEqual(switchOptions(1), [["", "1"]]);
  assert.deepEqual(switchOptions(3), [
    ["1", "1"],
    ["2", "2"],
    ["", "3"],
  ]);
  assert.deepEqual(switchOptions(5), [
    ["1", "1"],
    ["2", "2"],
    ["3", "3"],
    ["4", "4"],
    ["", "5"],
  ]);
  assert.deepEqual(switchOptions(7), [
    ["1", "1"],
    ["2", "2"],
    ["3", "3"],
    ["4", "4"],
    ["", "N"],
  ]);
});
