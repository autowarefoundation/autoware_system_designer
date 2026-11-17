// Unit tests for the replay bag file reader: MCAP summary, file choice and dropped paths.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

global.window = { SD: {} };
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
    "bag_file.js",
  ),
);
const bagFile = global.window.SD.activities.replay.bagFile;

const MAGIC = [0x89, 0x4d, 0x43, 0x41, 0x50, 0x30, 0x0d, 0x0a];

function u16(value) {
  const bytes = Buffer.alloc(2);
  bytes.writeUInt16LE(value);
  return bytes;
}

function u32(value) {
  const bytes = Buffer.alloc(4);
  bytes.writeUInt32LE(value);
  return bytes;
}

function u64(value) {
  const bytes = Buffer.alloc(8);
  bytes.writeBigUInt64LE(BigInt(value));
  return bytes;
}

function str(text) {
  return Buffer.concat([u32(Buffer.byteLength(text)), Buffer.from(text)]);
}

function record(op, ...parts) {
  const body = Buffer.concat(parts);
  return Buffer.concat([Buffer.from([op]), u64(body.length), body]);
}

function schema(id, name) {
  return record(0x03, u16(id), str(name), str("ros2msg"), u32(0));
}

function channel(id, schemaId, topic) {
  return record(0x04, u16(id), u16(schemaId), str(topic), str("cdr"), u32(0));
}

function statistics(counts) {
  const pairs = Buffer.concat(
    Object.entries(counts).map(([id, count]) =>
      Buffer.concat([u16(Number(id)), u64(count)]),
    ),
  );
  return record(
    0x0b,
    u64(0),
    u16(0),
    u32(0),
    u32(0),
    u32(0),
    u32(0),
    u64(0),
    u64(0),
    u32(pairs.length),
    pairs,
  );
}

// An MCAP file of a data section of `dataSize` bytes and the summary records.
function mcap(summary, dataSize = 16) {
  const head = Buffer.concat([Buffer.from(MAGIC), Buffer.alloc(dataSize)]);
  const body = Buffer.concat(summary);
  const footer = record(
    0x02,
    u64(body.length ? head.length : 0),
    u64(0),
    u32(0),
  );
  const file = new Blob([head, body, footer, Buffer.from(MAGIC)]);
  file.name = "bag_0.mcap";
  return file;
}

test("mcapTopics reads topics, types and counts from the summary section", async () => {
  const topics = await bagFile.mcapTopics(
    mcap([
      schema(1, "std_msgs/msg/String"),
      schema(2, "tf2_msgs/msg/TFMessage"),
      channel(1, 1, "/chatter"),
      channel(2, 2, "/tf"),
      channel(3, 2, "/tf"),
      record(0x08, Buffer.alloc(12)),
      statistics({ 1: 7, 2: 30, 3: 12 }),
    ]),
  );
  assert.deepEqual(
    [...topics],
    [
      ["/chatter", { type: "std_msgs/msg/String", count: 7 }],
      ["/tf", { type: "tf2_msgs/msg/TFMessage", count: 42 }],
    ],
  );
});

test("mcapTopics refuses a file without a footer or a summary section", async () => {
  const cut = new Blob([Buffer.from(MAGIC), Buffer.alloc(64)]);
  cut.name = "cut.mcap";
  await assert.rejects(bagFile.mcapTopics(cut), /not a finished MCAP file/);
  await assert.rejects(bagFile.mcapTopics(mcap([])), /without a summary/);
});

test("pick prefers the metadata, then an MCAP file", () => {
  const files = ["notes.txt", "bag_0.db3", "bag_0.mcap", "metadata.yaml"].map(
    (name) => ({ name }),
  );
  assert.equal(bagFile.pick(files).name, "metadata.yaml");
  assert.equal(bagFile.pick(files.slice(0, 3)).name, "bag_0.mcap");
  assert.equal(bagFile.pick(files.slice(0, 2)).name, "bag_0.db3");
  assert.equal(bagFile.pick(files.slice(0, 1)), null);
});

test("readTopics reads by file kind", async () => {
  const metadata = { name: "metadata.yaml", text: async () => "yaml" };
  assert.equal(
    await bagFile.readTopics(metadata, (text) => `${text}!`),
    "yaml!",
  );
  await assert.rejects(
    bagFile.readTopics({ name: "bag_0.db3" }),
    /metadata\.yaml/,
  );
  await assert.rejects(
    bagFile.readTopics({ name: "notes.txt" }),
    /not a metadata/,
  );
});

test("droppedPath takes a file URI or an absolute path", () => {
  assert.equal(
    bagFile.droppedPath("# comment\r\nfile:///data/my%20bag/bag_0.mcap\r\n"),
    "/data/my bag/bag_0.mcap",
  );
  assert.equal(bagFile.droppedPath("/data/bag"), "/data/bag");
  assert.equal(bagFile.droppedPath("~/bag"), "~/bag");
  assert.equal(bagFile.droppedPath("https://example.com/bag"), "");
  assert.equal(bagFile.droppedPath("some text"), "");
  assert.equal(bagFile.droppedPath(""), "");
});
