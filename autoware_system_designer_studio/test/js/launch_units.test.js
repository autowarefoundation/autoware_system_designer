// Unit tests for the launch unit logic: what a unit offers from its reach and
// sessions, and the labels of the peer daemon serving it.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const PART = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "activities",
  "launch",
  "units.js",
);

global.window = { SD: { activities: {} } };
require(PART);
const { unitActions, peerLabel, peerTitle } =
  global.window.SD.activities.launch;

const RUNNING = {
  state: "running",
  connected: true,
  control: { measure_armed: true },
};

test("an available unit without a session launches", () => {
  const actions = unitActions({ status: "available", session: null });
  assert.equal(actions.canLaunch, true);
  assert.equal(actions.canShutdown, false);
  assert.equal(actions.canMeasure, false);
});

test("a unit held by another session does not launch", () => {
  const holder = { mode: "other", compute_unit: "sub_ecu_1" };
  const actions = unitActions({ status: "available", session: null, holder });
  assert.equal(actions.canLaunch, false);
});

test("an exited session leaves the unit launchable", () => {
  const session = { state: "exited", exit_code: 0 };
  const actions = unitActions({ status: "available", session });
  assert.equal(actions.active, false);
  assert.equal(actions.canLaunch, true);
});

test("a running session shuts down and measures", () => {
  const actions = unitActions({ status: "running", session: RUNNING });
  assert.equal(actions.canLaunch, false);
  assert.equal(actions.canShutdown, true);
  assert.equal(actions.canMeasure, true);
});

test("a session shutting down takes no second shutdown", () => {
  const session = { ...RUNNING, shutting_down: true };
  const actions = unitActions({ status: "running", session });
  assert.equal(actions.canShutdown, false);
});

test("an unreachable unit offers nothing, with or without a session", () => {
  [null, RUNNING].forEach((session) => {
    const actions = unitActions({ status: "unreachable", session });
    assert.equal(actions.reachable, false);
    assert.equal(actions.canLaunch, false);
    assert.equal(actions.canShutdown, false);
    assert.equal(actions.canMeasure, false);
  });
});

test("a peer is labelled by its SSH destination, else by its address", () => {
  const url = "http://127.0.0.1:8782/";
  assert.equal(peerLabel({ url, ssh: "asd@10.0.0.12" }), "asd@10.0.0.12");
  assert.equal(peerLabel({ url, ssh: null }), "127.0.0.1:8782");
  assert.equal(peerLabel(undefined), "");
});

test("the peer title lists address, state, version and differences", () => {
  const title = peerTitle({
    url: "http://127.0.0.1:8782/",
    ssh: "asd@10.0.0.12",
    state: "unreachable",
    error: "connection refused",
    version: "0.4.0",
    mismatch: ["version 0.4.0, this daemon 0.5.0"],
  });
  assert.deepEqual(title.split("\n"), [
    "http://127.0.0.1:8782/ over ssh asd@10.0.0.12",
    "unreachable: connection refused",
    "version 0.4.0",
    "version 0.4.0, this daemon 0.5.0",
  ]);
});
