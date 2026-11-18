// Request-body builders for every mutating API call. Keeping them pure and
// free of React lets the contract test (scripts/check-bodies.mjs) import the
// same code the screens run and validate the bodies against openapi.json.
import type { SolveParams, SolveRequest, SpotSpec } from "./client";

export interface SpotForm {
  name: string;
  board: string[];
  dead: string[];
  range0: string;
  range1: string;
  pot: string;
  stack: string;
  maxNodes: string;
  allIn: boolean;
  sizeMode: "set" | "custom" | "players" | "streets";
  sizeSet: SpotSpec["bet_sizes"] & string;
  custom: string;
  p0sizes: string;
  p1sizes: string;
  turnSizes: string;
  riverSizes: string;
  raiseCap: number;
  raiseSizes: { kind: "x" | "p"; value: string }[];
}

export interface ParamsForm {
  dtype: "float32" | "float64";
  variant: SolveParams["variant"];
  priority: string;
  timeout: string;
  stop: "iterations" | "time_budget" | "target_exploitability";
  iterations: string;
  timeBudget: string;
  targetExpl: string;
  maxIterations: string;
  progressEvery: string;
  solverOptions: string;
}

export function parseFractions(text: string): number[] {
  const vals = text.split(/[,\s]+/).filter(Boolean).map(Number);
  if (!vals.length || vals.some((v) => !Number.isFinite(v) || v <= 0))
    throw new Error("bet sizes must be positive pot fractions");
  return vals;
}

export function buildSpot(f: SpotForm): SpotSpec {
  const spot: SpotSpec = {
    board: f.board.join(""),
    range0: f.range0,
    range1: f.range1,
    pot: Number(f.pot),
    stack: Number(f.stack),
    all_in: f.allIn,
    raise_cap: f.raiseCap,
    raise_sizes: f.raiseCap > 0 ? f.raiseSizes.map((r) => [r.kind, Number(r.value)]) : [],
    dead_cards: f.dead,
    bet_sizes: f.sizeSet,
    max_nodes: null,
  };
  if (f.name.trim()) spot.name = f.name.trim();
  if (f.maxNodes.trim()) spot.max_nodes = Number(f.maxNodes);
  switch (f.sizeMode) {
    case "set":
      spot.bet_sizes = f.sizeSet;
      break;
    case "custom":
      spot.bet_sizes = parseFractions(f.custom);
      break;
    case "players":
      spot.bet_sizes = { "0": parseFractions(f.p0sizes), "1": parseFractions(f.p1sizes) };
      break;
    case "streets":
      spot.bet_sizes = { turn: parseFractions(f.turnSizes), river: parseFractions(f.riverSizes) };
      break;
  }
  return spot;
}

export function buildParams(f: ParamsForm): SolveParams {
  const params: SolveParams = { variant: f.variant, dtype: f.dtype, priority: Number(f.priority) };
  if (f.solverOptions.trim()) {
    const opts = JSON.parse(f.solverOptions);
    if (typeof opts !== "object" || opts === null || Array.isArray(opts))
      throw new Error("solver options must be a JSON object");
    params.solver_options = opts;
  }
  if (f.timeout.trim()) params.timeout_seconds = Number(f.timeout);
  if (f.progressEvery.trim()) params.progress_every = Number(f.progressEvery);
  switch (f.stop) {
    case "iterations":
      params.iterations = Number(f.iterations);
      break;
    case "time_budget":
      params.time_budget = Number(f.timeBudget);
      break;
    case "target_exploitability":
      params.target_exploitability = Number(f.targetExpl);
      params.max_iterations = Number(f.maxIterations);
      break;
  }
  return params;
}

export function buildSolveRequest(spot: SpotForm, params: ParamsForm): SolveRequest {
  return { ...buildSpot(spot), ...buildParams(params) };
}
