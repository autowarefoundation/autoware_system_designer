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
const { unitActions, unitHolder, holderLabel, peerLabel, peerTitle } =
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

test("the last reported session of an unreachable unit can only be forgotten", () => {
  const actions = unitActions({ status: "unreachable", session: RUNNING });
  assert.equal(actions.canForget, true);
  assert.equal(actions.canShutdown, false);
  assert.equal(actions.canLaunch, false);
  const exited = { state: "exited" };
  assert.equal(
    unitActions({ status: "unreachable", session: exited }).canForget,
    false,
  );
  assert.equal(
    unitActions({ status: "running", session: RUNNING }).canForget,
    false,
  );
  // A live session of another mode, last reported for the unit.
  assert.equal(
    unitActions({ status: "unreachable", session: null, reported: true })
      .canForget,
    true,
  );
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

test("an unreachable unit launches, shuts down and measures nothing", () => {
  [null, RUNNING].forEach((session) => {
    const actions = unitActions({ status: "unreachable", session });
    assert.equal(actions.reachable, false);
    assert.equal(actions.canLaunch, false);
    assert.equal(actions.canShutdown, false);
    assert.equal(actions.canMeasure, false);
  });
});

const SHOWN = { system: "sys", mode: "main", unit: "main_ecu" };

function sessionOn(compute_unit, fields = {}) {
  return {
    system: "sys",
    mode: "main",
    compute_unit,
    state: "running",
    ...fields,
  };
}

test("a unit of the shown mode running elsewhere holds no launch", () => {
  assert.equal(unitHolder([sessionOn("sub_ecu_1")], SHOWN), null);
});

test("a unit waits until every unit of the system has shut down", () => {
  const requested = sessionOn("sub_ecu_1", { shutting_down: true });
  const stopping = sessionOn("sub_ecu_2", { state: "stopping" });
  const exited = sessionOn("main_ecu", { state: "exited" });
  assert.equal(unitHolder([exited, requested], SHOWN), requested);
  assert.equal(unitHolder([exited, stopping], SHOWN), stopping);
  const done = [requested, stopping].map((s) => ({ ...s, state: "exited" }));
  assert.equal(unitHolder([exited, ...done], SHOWN), null);
  assert.equal(holderLabel(requested, "main_ecu"), "sub_ecu_1 shutting down");
});

test("another mode of the system or another launch on the unit holds it", () => {
  const otherMode = sessionOn("sub_ecu_1", { mode: "other" });
  assert.equal(unitHolder([otherMode], SHOWN), otherMode);
  assert.equal(holderLabel(otherMode, "main_ecu"), "other runs on sub_ecu_1");
  const otherSystem = sessionOn("main_ecu", { system: "else" });
  assert.equal(unitHolder([otherSystem], SHOWN), otherSystem);
  assert.equal(holderLabel(otherSystem, "main_ecu"), "in use · main");
  assert.equal(
    unitHolder([sessionOn("sub_ecu_1", { system: "else" })], SHOWN),
    null,
  );
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
