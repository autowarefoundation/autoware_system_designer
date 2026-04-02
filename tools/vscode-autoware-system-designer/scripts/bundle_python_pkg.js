#!/usr/bin/env node
// Copies the autoware_system_designer Python package into server/bundled/
// so that the VSIX works without access to the source repository.
const fs = require("fs");
const path = require("path");

const extensionRoot = path.join(__dirname, "..");
const src = path.join(
  extensionRoot,
  "..",
  "..",
  "autoware_system_designer",
  "autoware_system_designer",
);
const dest = path.join(
  extensionRoot,
  "server",
  "bundled",
  "autoware_system_designer",
);

if (!fs.existsSync(src)) {
  console.error(`Source package not found: ${src}`);
  process.exit(1);
}

fs.rmSync(path.join(extensionRoot, "server", "bundled"), {
  recursive: true,
  force: true,
});
fs.mkdirSync(path.join(extensionRoot, "server", "bundled"), {
  recursive: true,
});
fs.cpSync(src, dest, { recursive: true });
console.log(`Bundled autoware_system_designer -> ${dest}`);
