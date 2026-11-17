// Unit tests for the replay run panel's phase segments.
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
    "run_panel.js",
  ),
);
const { phaseStates } = global.window.SD.activities.replay;

function states(job) {
  return phaseStates(job).map(({ phase, state, width }) => [phase.name, state, width]);
}

function job(state, phases, current, params = {}) {
  return {
    state,
    params,
    detail: phases ? { phases, progress: { phase: current, step: "", fraction: null } } : {},
  };
}

test("a running job without progress is on its pre-process", () => {
  assert.deepEqual(states(job("running")), [
    ["pre-process", "active", null],
    ["replay", "pending", 0],
    ["post-process", "pending", 0],
  ]);
});

test("a finished job without progress has no bar", () => {
  assert.equal(phaseStates(job("succeeded")), null);
});

test("the replay phase fills by its fraction after the pre-process", () => {
  const running = job("running", { "pre-process": 1, replay: 0.4 }, "replay");
  assert.deepEqual(states(running), [
    ["pre-process", "done", 1],
    ["replay", "active", 0.4],
    ["post-process", "pending", 0],
  ]);
});

test("a replay cut short stays stopped while its post-process completes", () => {
  const failed = job(
    "failed",
    { "pre-process": 1, replay: 0.4, "post-process": 1 },
    "post-process",
  );
  assert.deepEqual(states(failed), [
    ["pre-process", "done", 1],
    ["replay", "stopped", 0.4],
    ["post-process", "done", 1],
  ]);
});

test("a scope that never came up stops in its pre-process", () => {
  const failed = job("failed", { "pre-process": null, "post-process": 1 }, "post-process");
  assert.deepEqual(states(failed), [
    ["pre-process", "stopped", null],
    ["replay", "pending", 0],
    ["post-process", "done", 1],
  ]);
});

test("a pre-flight has its pre-process only", () => {
  const passed = job("succeeded", { "pre-process": 1 }, "pre-process", {
    check: true,
  });
  assert.deepEqual(states(passed), [["pre-process", "done", 1]]);
});
