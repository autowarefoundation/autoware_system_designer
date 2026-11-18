// Unit tests for the replay zones: how a divider shares height between zones.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

global.window = { SD: { widgets: {}, activities: {} } };
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
    "zones.js",
  ),
);
const { resizeZones } = global.window.SD.activities.replay;

test("a divider moves height between the zones beside it", () => {
  assert.deepEqual(
    resizeZones([400, 200, 200], [false, false, false], 0, 50),
    [450, 150, 200],
  );
});

test("a folded zone passes the divider on to the next expanded one", () => {
  assert.deepEqual(
    resizeZones([400, 30, 200], [false, true, false], 1, -100),
    [300, 30, 300],
  );
});

test("each side keeps its least height", () => {
  assert.deepEqual(
    resizeZones([400, 200, 200], [false, false, false], 1, 500, 90),
    [400, 310, 90],
  );
});

test("a divider with no expanded zone on a side moves nothing", () => {
  const heights = [30, 400, 200];
  assert.equal(resizeZones(heights, [true, false, false], 0, 50), heights);
});
