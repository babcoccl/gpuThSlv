#!/usr/bin/env node
// Contract check: renaming a request-body field in the server schema must
// break tsc. Mutates SpotSpec.range0 -> range0_renamed in openapi.json,
// regenerates schema.d.ts, runs tsc and asserts failure, then restores both
// files and regenerates. Exits 0 only when tsc actually failed.
import { execFileSync, execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ui = join(dirname(fileURLToPath(import.meta.url)), "..");
const openapiPath = join(ui, "src/api/openapi.json");
const schemaPath = join(ui, "src/api/schema.d.ts");
const openapiOrig = readFileSync(openapiPath, "utf8");
const schemaOrig = readFileSync(schemaPath, "utf8");

const openapi = JSON.parse(openapiOrig);
const props = openapi.components.schemas.SpotSpec.properties;
if (!("range0" in props)) throw new Error("openapi.json SpotSpec has no range0 — mutation target gone");
props.range0_renamed = props.range0;
delete props.range0;
openapi.components.schemas.SpotSpec.required = openapi.components.schemas.SpotSpec.required.map((f) =>
  f === "range0" ? "range0_renamed" : f,
);
writeFileSync(openapiPath, JSON.stringify(openapi, null, 2) + "\n");

let tscFailed = false;
try {
  execSync("npx openapi-typescript src/api/openapi.json -o src/api/schema.d.ts", { cwd: ui, stdio: "inherit" });
  execFileSync("npx", ["tsc", "-b"], { cwd: ui, stdio: "pipe" });
} catch {
  tscFailed = true;
} finally {
  writeFileSync(openapiPath, openapiOrig);
  writeFileSync(schemaPath, schemaOrig);
}

if (!tscFailed) {
  console.error("FAIL: tsc passed with a renamed request field — the UI is not type-bound to the schema");
  process.exit(1);
}
console.log("check-mutation: tsc failed on renamed SpotSpec.range0 as required");
