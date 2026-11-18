#!/usr/bin/env node
// Contract check: every request body the UI builds must validate against the
// OpenAPI request schema. Imports the real builders from src/api/payloads.ts
// (Node >=22 strips the types) and validates each body with ajv against the
// $ref'd component schema in src/api/openapi.json.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";

const ui = join(dirname(fileURLToPath(import.meta.url)), "..");
const openapi = JSON.parse(readFileSync(join(ui, "src/api/openapi.json"), "utf8"));

const { buildSpot, buildParams, buildSolveRequest } = await import("../src/api/payloads.ts");

// Re-root #/components/schemas/* refs so ajv can resolve them inside $defs.
const rewriteRefs = (node) => {
  if (Array.isArray(node)) return node.map(rewriteRefs);
  if (node && typeof node === "object") {
    const out = {};
    for (const [k, v] of Object.entries(node))
      out[k] = k === "$ref" && typeof v === "string" ? v.replace("#/components/schemas/", "#/$defs/") : rewriteRefs(v);
    return out;
  }
  return node;
};
const defs = rewriteRefs(openapi.components.schemas);

const ajv = new Ajv2020({ strict: false, allErrors: true });
const validator = (name) => ajv.compile({ $defs: defs, $ref: `#/$defs/${name}` });

const spotForm = {
  name: "fixture-river",
  board: ["As", "Kd", "Qh", "Jc", "2s"],
  dead: [],
  range0: "AA:1.0,KK:0.5,random:0.1",
  range1: "random",
  pot: "20",
  stack: "100",
  maxNodes: "5000",
  allIn: false,
  sizeMode: "custom",
  sizeSet: "standard",
  custom: "0.5, 1.0",
  p0sizes: "0.5, 1.0",
  p1sizes: "0.5, 1.0",
  turnSizes: "0.5",
  riverSizes: "1.0",
  raiseCap: 1,
  raiseSizes: [{ kind: "x", value: "2.0" }],
};
const turnSpotForm = {
  ...spotForm,
  board: ["As", "Kd", "Qh", "Jc"],
  sizeMode: "streets",
};
// Union-branch fixtures: bet_sizes is a union of preset | list | per-player
// | per-street; raise_sizes entries carry kind "x" or "p". One fixture per
// branch — the coverage assertion below fails when a branch has none.
const presetSpotForm = { ...spotForm, sizeMode: "set" };
const playersSpotForm = { ...spotForm, sizeMode: "players" };
const raisePSpotForm = {
  ...spotForm, raiseSizes: [{ kind: "p", value: "0.75" }],
};
const paramsForm = {
  dtype: "float32",
  variant: "cfr_plus",
  priority: "0",
  timeout: "",
  stop: "iterations",
  iterations: "500",
  timeBudget: "60",
  targetExpl: "0.002",
  maxIterations: "20000",
  progressEvery: "10",
  solverOptions: "{}",
};

const cases = [
  ["POST /v1/solves", "SolveRequest", buildSolveRequest(spotForm, paramsForm)],
  ["POST /v1/solves (turn, per-street sizes)", "SolveRequest", buildSolveRequest(turnSpotForm, paramsForm)],
  ["POST /v1/solves (preset sizes)", "SolveRequest", buildSolveRequest(presetSpotForm, paramsForm)],
  ["POST /v1/solves (per-player sizes)", "SolveRequest", buildSolveRequest(playersSpotForm, paramsForm)],
  ["POST /v1/solves (raise kind p)", "SolveRequest", buildSolveRequest(raisePSpotForm, paramsForm)],
  ["POST /v1/estimate", "EstimateRequest", { spot: buildSpot(spotForm), dtype: "float32" }],
  ["POST /v1/spots", "SpotCreate", { name: "fixture", spot: buildSpot(spotForm), tags: ["t"], notes: "n" }],
  ["PATCH /v1/spots/{id}", "SpotUpdate", { name: "renamed", tags: ["x"], spot: buildSpot(spotForm) }],
  ["POST /v1/spots/{id}/solve", "SolveParams", buildParams(paramsForm)],
  ["POST /v1/auth/login", "LoginRequest", { key: "deadbeef" }],
];

let fail = 0;
for (const [label, schemaName, body] of cases) {
  const validate = validator(schemaName);
  if (validate(body)) {
    console.log(`ok ${label} -> ${schemaName}`);
  } else {
    console.error(`FAIL ${label} -> ${schemaName}: ${ajv.errorsText(validate.errors)}`);
    fail++;
  }
}
if (fail) process.exit(1);

// Union-branch coverage: SpotSpec.bet_sizes is anyOf(preset | list |
// per-player | per-street); each raise_sizes entry is [kind, value] with
// kind in {"x", "p"}. Every union branch must be exercised by at least one
// built body — a branch with no fixture fails this check.
const spotSchema = openapi.components.schemas.SpotSpec;
const betBranches = spotSchema.properties.bet_sizes.anyOf ?? [spotSchema.properties.bet_sizes];
const raiseEnum = spotSchema.properties.raise_sizes.items.anyOf?.[0]?.enum
  ?? spotSchema.properties.raise_sizes.items.prefixItems?.[0]?.enum ?? [];

const classifyBet = (v) => {
  if (typeof v === "string") return "preset";
  if (Array.isArray(v)) return "list";
  if (v && typeof v === "object") {
    const keys = Object.keys(v).sort().join(",");
    if (keys === "0,1") return "players";
    if (keys === "river,turn") return "streets";
    return `map:${keys}`;
  }
  return "other";
};
const resolveRef = (s) =>
  s.$ref ? openapi.components.schemas[s.$ref.split("/").pop()] ?? s : s;
const branchName = (s) => {
  s = resolveRef(s);
  if (s.enum) return "preset";
  if (s.type === "array") return "list";
  if (s.type === "object") {
    const props = Object.keys(s.properties ?? {}).sort().join(",");
    if (props === "0,1") return "players";
    if (props === "river,turn") return "streets";
    return `map:${props}`;
  }
  return `schema:${s.$ref ?? s.type ?? "?"}`;
};

const coveredBets = new Set();
const coveredRaiseKinds = new Set();
for (const [, , body] of cases) {
  const spec = body.spot ?? body;
  if (spec.bet_sizes !== undefined) coveredBets.add(classifyBet(spec.bet_sizes));
  for (const r of spec.raise_sizes ?? []) coveredRaiseKinds.add(r[0]);
}
let coverageFail = 0;
for (const branch of betBranches) {
  const want = branchName(branch);
  if (!coveredBets.has(want)) {
    console.error(`FAIL coverage: no fixture exercises bet_sizes union branch ${want}`);
    coverageFail++;
  }
}
for (const kind of raiseEnum) {
  if (!coveredRaiseKinds.has(kind)) {
    console.error(`FAIL coverage: no fixture exercises raise_sizes kind "${kind}"`);
    coverageFail++;
  }
}
if (coverageFail) process.exit(1);
console.log(`check-bodies: all request bodies validate against openapi.json; union branches covered: bets=${[...coveredBets].sort().join(",")} raiseKinds=${[...coveredRaiseKinds].sort().join(",")}`);
