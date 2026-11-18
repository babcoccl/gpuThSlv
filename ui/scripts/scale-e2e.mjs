// Scale run (Phase 10.1 item 6): solve three demanding spots on the outpost's
// real GPUs, then open each in the viewer and record /tree + /node latency,
// response sizes, time to first render, and console errors.
// Usage: node scripts/scale-e2e.mjs  (reads HOLDEM_API_KEY from holdem.env)
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.HOLDEM_UI_BASE ?? "http://127.0.0.1:8000";
const OUT = path.resolve(process.env.HOLDEM_UI_LOG_DIR ?? "../scripts/phase10/logs");
const envText = fs.readFileSync(path.join(process.env.HOME, ".config/holdem-solver/holdem.env"), "utf8");
const KEY = envText.match(/^HOLDEM_API_KEY=(.+)$/m)?.[1]?.trim();
if (!KEY) throw new Error("HOLDEM_API_KEY not found in holdem.env");
fs.mkdirSync(OUT, { recursive: true });

const H = { "x-api-key": KEY };
async function post(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { ...H, "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
}
async function getTimed(path) {
  const t0 = performance.now();
  const r = await fetch(`${BASE}${path}`, { headers: H });
  const text = await r.text();
  const ms = performance.now() - t0;
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}: ${text.slice(0, 200)}`);
  return { ms, bytes: Buffer.byteLength(text) };
}
async function waitDone(id, timeoutMs = 900_000) {
  const t0 = Date.now();
  for (;;) {
    const { ms, bytes } = await getTimed(`/v1/solves/${id}`);
    const st = JSON.parse(await (await fetch(`${BASE}/v1/solves/${id}`, { headers: H })).text());
    if (st.status === "done") return { poll: ms, job: st, bytes };
    if (["failed", "cancelled"].includes(st.status)) throw new Error(`job ${id} ${st.status}: ${st.error ?? ""}`);
    if (Date.now() - t0 > timeoutMs) throw new Error(`job ${id} still ${st.status}`);
    await new Promise((r) => setTimeout(r, 3000));
  }
}

const BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo";
const MID = "TT+, AQs+, AKo, KQs";
const spots = [
  {
    name: "scale-river-573k",
    body: {
      board: "KsJsTh7d2c", range0: BROAD, range1: BROAD, pot: 20, stack: 100,
      bet_sizes: "standard", iterations: 200, progress_every: 25, dtype: "float32",
    },
  },
  {
    name: "scale-turn-chance",
    body: {
      board: "KsJsTh7d", range0: "QQ+, AKs", range1: "QQ+, AKs", pot: 20, stack: 100,
      bet_sizes: { turn: [0.5, 1.0], river: [1.0] }, iterations: 200,
      progress_every: 25, dtype: "float32",
    },
  },
  {
    name: "scale-raisecap2",
    body: {
      board: "KsJsTh7d2c", range0: MID, range1: MID, pot: 20, stack: 100,
      bet_sizes: [0.5, 1.0], raise_cap: 2, raise_sizes: [["x", 2.0], ["p", 0.75]],
      iterations: 200, progress_every: 25, dtype: "float32",
    },
  },
];

const results = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const uiErrors = [];
page.on("console", (m) => m.type === "error" && uiErrors.push(m.text()));
page.on("pageerror", (e) => uiErrors.push(String(e)));

try {
  // Login once for the browser pass.
  await page.goto(`${BASE}/ui/login`);
  await page.getByLabel("API key").fill(KEY);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/ui\/jobs/);

  for (const s of spots) {
    const acc = await post("/v1/solves", s.body);
    console.log(`${s.name}: queued ${acc.id}`);
    const tSubmit = Date.now();
    const { job } = await waitDone(acc.id);
    console.log(`${s.name}: done in ${((Date.now() - tSubmit) / 1000).toFixed(0)}s, nodes=${job.num_nodes}`);

    const measures = [];
    const edge = async (hist) => `history=${encodeURIComponent(hist.join(","))}`;
    const treeOf = async (hist) => JSON.parse(await (await fetch(`${BASE}/v1/solves/${acc.id}/tree?${await edge(hist)}`, { headers: H })).text());
    // Cold /node FIRST: the per-job index + persisted EVs load on the
    // first request for this job; a /tree call first would warm the cache
    // and understate first-hit latency.
    const q0 = await edge([]);
    try {
      const firstNode = await getTimed(`/v1/solves/${acc.id}/node?${q0}`);
      const warmNode = await getTimed(`/v1/solves/${acc.id}/node?${q0}`);
      const rootTree = await getTimed(`/v1/solves/${acc.id}/tree?${q0}`);
      measures.push({ history: "root", first_node_ms: firstNode.ms, warm_node_ms: warmNode.ms, tree_ms: rootTree.ms, node_bytes: firstNode.bytes, tree_bytes: rootTree.bytes });
    } catch (e) {
      measures.push({ history: "root", first_node_ms: null, warm_node_ms: null, tree_ms: null, node_error: String(e).slice(0, 120) });
    }
    // Discover real paths: root -> first non-terminal child -> grandchild.
    const paths = [];
    let cur = await treeOf([]);
    for (let depth = 0; depth < 2 && cur.children?.length; depth++) {
      const next = cur.children.find((c) => c.kind !== "terminal" && c.history);
      if (!next) break;
      paths.push(next.history);
      cur = await treeOf(next.history);
    }
    for (const h of paths) {
      const q = await edge(h);
      const tree = await getTimed(`/v1/solves/${acc.id}/tree?${q}`);
      try {
        const node = await getTimed(`/v1/solves/${acc.id}/node?${q}`);
        measures.push({ history: h.join("/"), tree_ms: tree.ms, tree_bytes: tree.bytes, node_ms: node.ms, node_bytes: node.bytes });
      } catch (e) {
        measures.push({ history: h.join("/"), tree_ms: tree.ms, tree_bytes: tree.bytes, node_ms: null, node_bytes: null, node_error: String(e).slice(0, 120) });
      }
    }

    // Time to first render in the viewer.
    uiErrors.length = 0;
    const t0 = performance.now();
    await page.goto(`${BASE}/ui/jobs/${acc.id}`);
    await page.getByTestId("tree-node").waitFor({ timeout: 60_000 });
    const ttfr = performance.now() - t0;
    await shot(page, `scale-${s.name}`);
    results.push({
      name: s.name, id: acc.id, nodes: job.num_nodes,
      build_s: job.build_seconds, solve_s: job.elapsed_seconds,
      scenario_wall_s: (Date.now() - tSubmit) / 1000,
      iterations: job.iterations_run, exploitability: job.exploitability,
      ttfr_ms: ttfr, calls: measures, ui_errors: [...uiErrors],
    });
    console.log(`${s.name}: ttfr=${ttfr.toFixed(0)}ms uiErrors=${uiErrors.length}`);
  }
} finally {
  await browser.close();
}
async function shot(p, name) {
  await p.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}
fs.writeFileSync(path.join(OUT, "scale-10-2.json"), JSON.stringify(results, null, 2));
for (const r of results) {
  console.log(`\n== ${r.name} (nodes=${r.nodes}, build=${r.build_s?.toFixed(1)}s, solve=${r.solve_s?.toFixed(1)}s, iters=${r.iterations}, expl=${r.exploitability}, ttfr=${r.ttfr_ms.toFixed(0)}ms, ui_errors=${r.ui_errors.length})`);
  for (const m of r.calls) {
    const nodeTxt = m.first_node_ms !== undefined
      ? `first ${m.first_node_ms === null ? `ERR ${m.node_error}` : `${m.first_node_ms.toFixed(0)}ms`} warm ${m.warm_node_ms?.toFixed(0)}ms`
      : `node ${m.node_ms === null ? `ERR ${m.node_error}` : `${m.node_ms.toFixed(0)}ms`}`;
    console.log(`  ${m.history}: tree ${m.tree_ms === null ? "-" : `${m.tree_ms.toFixed(0)}ms/${(m.tree_bytes / 1024).toFixed(1)}KiB`} ${nodeTxt}${m.node_bytes ? `/${(m.node_bytes / 1024).toFixed(1)}KiB` : ""}`);
  }
  for (const e of r.ui_errors) console.log(`  ui-error: ${e}`);
}
console.log("\nscale run complete");
