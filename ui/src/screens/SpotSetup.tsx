import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, EstimateResponse } from "../api/client";
import { buildParams, buildSpot } from "../api/payloads";
import CardPicker from "../components/CardPicker";
import RangeEditor from "../components/RangeEditor";
import { BET_SIZE_SETS, parseRange, serializeRange } from "../poker";

type SizeMode = "set" | "custom" | "players" | "streets";
type StopMode = "iterations" | "time_budget" | "target_exploitability";

interface FormState {
  name: string;
  board: string[];
  dead: string[];
  range0: Map<string, number>;
  range1: Map<string, number>;
  pot: string;
  stack: string;
  maxNodes: string;
  allIn: boolean;
  sizeMode: SizeMode;
  sizeSet: string;
  custom: string;
  p0sizes: string;
  p1sizes: string;
  turnSizes: string;
  riverSizes: string;
  raiseCap: number;
  raiseSizes: { kind: "x" | "p"; value: string }[];
  dtype: "float32" | "float64";
  variant: "cfr" | "cfr_plus" | "dcfr" | "pcfr_plus";
  priority: string;
  timeout: string;
  stop: StopMode;
  iterations: string;
  timeBudget: string;
  targetExpl: string;
  maxIterations: string;
  progressEvery: string;
  solverOptions: string;
  tags: string;
  notes: string;
}

const DEFAULTS: FormState = {
  name: "",
  board: [],
  dead: [],
  range0: new Map(),
  range1: new Map(),
  pot: "20",
  stack: "100",
  maxNodes: "",
  allIn: false,
  sizeMode: "set",
  sizeSet: "standard",
  custom: "0.5, 1.0",
  p0sizes: "0.5, 1.0",
  p1sizes: "0.5, 1.0",
  turnSizes: "0.5, 1.0",
  riverSizes: "0.5, 1.0",
  raiseCap: 0,
  raiseSizes: [{ kind: "x", value: "2.0" }],
  dtype: "float32",
  variant: "cfr_plus",
  priority: "0",
  timeout: "",
  stop: "iterations",
  iterations: "1000",
  timeBudget: "60",
  targetExpl: "0.002",
  maxIterations: "20000",
  progressEvery: "",
  solverOptions: "",
  tags: "",
  notes: "",
};

function fieldErrors(detail: unknown): { fields: [string, string][]; message: string | null } {
  if (Array.isArray(detail)) {
    const fields: [string, string][] = detail.map((e) => {
      const loc = Array.isArray(e?.loc) ? e.loc.filter((p: unknown) => p !== "body").join(".") : "";
      return [String(loc), String(e?.msg ?? e)];
    });
    return { fields, message: null };
  }
  return { fields: [], message: typeof detail === "string" ? detail : JSON.stringify(detail) };
}

export default function SpotSetup() {
  const { id } = useParams();
  const nav = useNavigate();
  const [f, setF] = useState<FormState>(DEFAULTS);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [errors, setErrors] = useState<{ fields: [string, string][]; message: string | null } | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [rawRanges, setRawRanges] = useState<[string, string] | null>(null);
  const [rangeDirty, setRangeDirty] = useState<[boolean, boolean]>([false, false]);

  useEffect(() => {
    if (!id) return;
    api.getSpot(id).then((rec) => {
      const s = rec.spot;
      const next = { ...DEFAULTS };
      setRawRanges([s.range0, s.range1]);
      next.name = rec.name;
      next.board = typeof s.board === "string" ? s.board.match(/../g) ?? [] : s.board;
      next.dead = s.dead_cards ?? [];
      try {
        next.range0 = parseRange(s.range0);
        next.range1 = parseRange(s.range1);
      } catch { /* keep empty; the editor shows what parsed */ }
      next.pot = String(s.pot);
      next.stack = String(s.stack);
      next.maxNodes = s.max_nodes ? String(s.max_nodes) : "";
      next.allIn = s.all_in;
      next.raiseCap = s.raise_cap;
      next.raiseSizes = s.raise_sizes?.map(([k, v]) => ({ kind: k, value: String(v) }))
        ?? [{ kind: "x", value: "2.0" }];
      next.tags = rec.tags.join(", ");
      next.notes = rec.notes;
      const bs = s.bet_sizes;
      if (typeof bs === "string" && bs in BET_SIZE_SETS) {
        next.sizeMode = "set";
        next.sizeSet = bs;
      } else if (Array.isArray(bs)) {
        next.sizeMode = "custom";
        next.custom = bs.join(", ");
      } else if (typeof bs === "object" && bs !== null) {
        if ("turn" in bs) {
          next.sizeMode = "streets";
          next.turnSizes = String(bs.turn);
          next.riverSizes = String(bs.river);
        } else {
          next.sizeMode = "players";
          next.p0sizes = String(bs["0"] ?? "");
          next.p1sizes = String(bs["1"] ?? "");
        }
      }
      setF(next);
      setLoadedName(rec.name);
    }).catch((err) => setErrors({ fields: [], message: String(err instanceof ApiError ? err.message : err) }));
  }, [id]);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((p) => ({ ...p, [k]: v }));

  const spotBody = () =>
    buildSpot({
      ...f,
      range0: rawRanges && !rangeDirty[0] ? rawRanges[0] : serializeRange(f.range0),
      range1: rawRanges && !rangeDirty[1] ? rawRanges[1] : serializeRange(f.range1),
    });
  const paramsBody = () => buildParams(f);

  const onError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 422) setErrors(fieldErrors(err.detail));
    else setErrors({ fields: [], message: err instanceof ApiError ? err.message : String(err) });
  };

  const doEstimate = async () => {
    setBusy(true);
    setErrors(null);
    setEstimate(null);
    try {
      setEstimate(await api.estimate(spotBody(), f.dtype));
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  };

  const doSolve = async () => {
    setBusy(true);
    setErrors(null);
    setInfo(null);
    try {
      const accepted = id
        ? await api.solveSpot(id, paramsBody())
        : await api.submitSolve({ ...spotBody(), ...paramsBody() });
      setInfo(`queued ${accepted.id}${accepted.queue_depth != null ? ` (depth ${accepted.queue_depth})` : ""}`);
      nav(`/jobs/${accepted.id}`);
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  };

  const doSave = async () => {
    setBusy(true);
    setErrors(null);
    setInfo(null);
    try {
      const tags = f.tags.split(",").map((t) => t.trim()).filter(Boolean);
      if (id) {
        await api.updateSpot(id, { name: f.name, spot: spotBody(), tags, notes: f.notes });
        setInfo("saved");
      } else {
        const rec = await api.createSpot({ name: f.name || "untitled", spot: spotBody(), tags, notes: f.notes });
        setInfo(`saved ${rec.id}`);
        nav(`/spots`);
      }
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  };

  const isTurn = f.board.length === 4;

  return (
    <div>
      <h1>{id ? `Edit spot — ${loadedName ?? id}` : "New spot"}</h1>

      <div className="cols">
        <div className="panel">
          <CardPicker
            label={`Board (${f.board.length} card${f.board.length === 1 ? "" : "s"}; 4 = turn, 5 = river)`}
            selected={f.board}
            max={5}
            onChange={(c) => set("board", c)}
          />
          {f.board.length > 0 && f.board.length < 4 && (
            <p className="error" role="alert">a spot needs 4 or 5 board cards</p>
          )}
          <div style={{ marginTop: 10 }}>
            <CardPicker
              label={`Dead cards (river only; ${f.dead.length} selected)`}
              selected={f.dead}
              disabled={f.board}
              max={8}
              onChange={(c) => set("dead", c)}
            />
            {!isTurn && f.dead.length > 0 && null}
            {isTurn && f.dead.length > 0 && (
              <p className="error">dead cards only apply to river spots</p>
            )}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <div><label htmlFor="pot">pot</label><input id="pot" type="number" min="0" step="any" value={f.pot} onChange={(e) => set("pot", e.target.value)} /></div>
            <div><label htmlFor="stack">stack</label><input id="stack" type="number" min="0" step="any" value={f.stack} onChange={(e) => set("stack", e.target.value)} /></div>
            <div><label htmlFor="name">name</label><input id="name" type="text" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="spot name" /></div>
          </div>
        </div>

        <div className="panel">
          <h3>Bet sizes (pot fractions)</h3>
          <div className="row">
            <div>
              <label htmlFor="size-mode">mode</label>
              <select id="size-mode" value={f.sizeMode} onChange={(e) => set("sizeMode", e.target.value as SizeMode)}>
                <option value="set">named set</option>
                <option value="custom">custom fractions</option>
                <option value="players">per player</option>
                <option value="streets" disabled={!isTurn}>per street (turn)</option>
              </select>
            </div>
            {f.sizeMode === "set" && (
              <div>
                <label htmlFor="size-set">set</label>
                <select id="size-set" value={f.sizeSet} onChange={(e) => set("sizeSet", e.target.value)}>
                  {Object.entries(BET_SIZE_SETS).map(([name, fracs]) => (
                    <option key={name} value={name}>{name} ({fracs.map((x) => x.toFixed(2)).join(", ")})</option>
                  ))}
                </select>
              </div>
            )}
            {f.sizeMode === "custom" && (
              <div className="grow"><label htmlFor="sizes-custom">fractions</label>
                <input id="sizes-custom" type="text" className="mono" value={f.custom} onChange={(e) => set("custom", e.target.value)} /></div>
            )}
            {f.sizeMode === "players" && (
              <>
                <div><label htmlFor="sizes-p0">player 0</label>
                  <input id="sizes-p0" type="text" className="mono" value={f.p0sizes} onChange={(e) => set("p0sizes", e.target.value)} /></div>
                <div><label htmlFor="sizes-p1">player 1</label>
                  <input id="sizes-p1" type="text" className="mono" value={f.p1sizes} onChange={(e) => set("p1sizes", e.target.value)} /></div>
              </>
            )}
            {f.sizeMode === "streets" && (
              <>
                <div><label htmlFor="sizes-turn">turn</label>
                  <input id="sizes-turn" type="text" className="mono" value={f.turnSizes} onChange={(e) => set("turnSizes", e.target.value)} /></div>
                <div><label htmlFor="sizes-river">river</label>
                  <input id="sizes-river" type="text" className="mono" value={f.riverSizes} onChange={(e) => set("riverSizes", e.target.value)} /></div>
              </>
            )}
            <div>
              <label htmlFor="all-in">all-in size</label>
              <select id="all-in" value={f.allIn ? "yes" : "no"} onChange={(e) => set("allIn", e.target.value === "yes")}>
                <option value="no">off</option>
                <option value="yes">on (stack/pot)</option>
              </select>
            </div>
          </div>

          <h3>Raises</h3>
          <div className="row">
            <div>
              <label htmlFor="raise-cap">raise cap / street</label>
              <input id="raise-cap" type="number" min={0} max={8} value={f.raiseCap} onChange={(e) => set("raiseCap", Number(e.target.value))} />
            </div>
            {f.raiseCap > 0 &&
              f.raiseSizes.map((r, i) => (
                <div key={i}>
                  <label htmlFor={`rs-${i}`}>raise {i + 1}</label>
                  <span className="row" style={{ gap: 4 }}>
                    <select id={`rs-${i}`} value={r.kind} onChange={(e) => set("raiseSizes", f.raiseSizes.map((x, j) => (j === i ? { ...x, kind: e.target.value as "x" | "p" } : x)))}>
                      <option value="x">× bet</option>
                      <option value="p">+ × pot</option>
                    </select>
                    <input type="number" min="0" step="any" value={r.value} style={{ width: 70 }}
                      onChange={(e) => set("raiseSizes", f.raiseSizes.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                    <button type="button" className="ghost small" onClick={() => set("raiseSizes", f.raiseSizes.filter((_, j) => j !== i))}>×</button>
                  </span>
                </div>
              ))}
            {f.raiseCap > 0 && (
              <button type="button" className="ghost small" onClick={() => set("raiseSizes", [...f.raiseSizes, { kind: "x", value: "2.0" }])}>
                + raise size
              </button>
            )}
          </div>

          <h3>Solver</h3>
          <div className="row">
            <div>
              <label htmlFor="variant">variant</label>
              <select id="variant" value={f.variant} onChange={(e) => set("variant", e.target.value as FormState["variant"])}>
                {["cfr", "cfr_plus", "dcfr", "pcfr_plus"].map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="dtype">dtype</label>
              <select id="dtype" value={f.dtype} onChange={(e) => set("dtype", e.target.value as "float32" | "float64")}>
                <option value="float32">float32</option>
                <option value="float64">float64</option>
              </select>
            </div>
            <div><label htmlFor="priority">priority</label><input id="priority" type="number" min={-100} max={100} value={f.priority} onChange={(e) => set("priority", e.target.value)} /></div>
            <div><label htmlFor="timeout">timeout s</label><input id="timeout" type="number" min="0" value={f.timeout} onChange={(e) => set("timeout", e.target.value)} placeholder="—" /></div>
            <div><label htmlFor="maxnodes">max nodes</label><input id="maxnodes" type="number" min="0" value={f.maxNodes} onChange={(e) => set("maxNodes", e.target.value)} placeholder="2M" /></div>
          </div>

          <h3>Stop conditions</h3>
          <div className="row">
            <div>
              <label htmlFor="stop">mode</label>
              <select id="stop" value={f.stop} onChange={(e) => set("stop", e.target.value as StopMode)}>
                <option value="iterations">iterations</option>
                <option value="time_budget">time budget (s)</option>
                <option value="target_exploitability">target exploitability</option>
              </select>
            </div>
            {f.stop === "iterations" && (
              <div><label htmlFor="iters">iterations</label><input id="iters" type="number" min="1" value={f.iterations} onChange={(e) => set("iterations", e.target.value)} /></div>
            )}
            {f.stop === "time_budget" && (
              <div><label htmlFor="tb">seconds</label><input id="tb" type="number" min="0" step="any" value={f.timeBudget} onChange={(e) => set("timeBudget", e.target.value)} /></div>
            )}
            {f.stop === "target_exploitability" && (
              <>
                <div><label htmlFor="te">target</label><input id="te" type="number" min="0" step="any" value={f.targetExpl} onChange={(e) => set("targetExpl", e.target.value)} /></div>
                <div><label htmlFor="mi">max iterations</label><input id="mi" type="number" min="1" value={f.maxIterations} onChange={(e) => set("maxIterations", e.target.value)} /></div>
              </>
            )}
            <div><label htmlFor="pe">progress every</label><input id="pe" type="number" min="1" value={f.progressEvery} onChange={(e) => set("progressEvery", e.target.value)} placeholder="—" /></div>
            <div className="grow"><label htmlFor="solver-opts">solver options (JSON)</label>
              <input id="solver-opts" type="text" className="mono" value={f.solverOptions} onChange={(e) => set("solverOptions", e.target.value)} placeholder="{}" /></div>
          </div>
        </div>
      </div>

      <div className="cols">
        <RangeEditor label="Player 0 (OOP, first to act)" weights={f.range0}
          onChange={(w) => { set("range0", w); setRangeDirty((d) => [true, d[1]]); }} />
        <RangeEditor label="Player 1 (IP)" weights={f.range1}
          onChange={(w) => { set("range1", w); setRangeDirty((d) => [d[0], true]); }} />
      </div>

      <div className="panel">
        <div className="row">
          <div className="grow"><label htmlFor="tags">tags (comma separated)</label>
            <input id="tags" type="text" value={f.tags} onChange={(e) => set("tags", e.target.value)} /></div>
          <div className="grow"><label htmlFor="notes">notes</label>
            <input id="notes" type="text" value={f.notes} onChange={(e) => set("notes", e.target.value)} /></div>
        </div>
      </div>

      {errors && (
        <div className="error" role="alert" data-testid="form-errors">
          {errors.message && <div>{errors.message}</div>}
          {errors.fields.length > 0 && (
            <ul>
              {errors.fields.map(([loc, msg], i) => (
                <li key={i}><code>{loc || "body"}</code>: {msg}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {info && <p className="status" data-testid="form-info">{info}</p>}

      {estimate && (
        <div className="panel" data-testid="estimate">
          <h3>Estimate ({estimate.street}, {estimate.weighted ? "weighted" : "uniform"} ranges)</h3>
          <table>
            <tbody>
              <tr><td>nodes</td><td className="mono">{estimate.num_nodes.toLocaleString()}</td></tr>
              <tr><td>deals</td><td className="mono">{estimate.num_deals.toLocaleString()}</td></tr>
              <tr><td>combos p0 / p1</td><td className="mono">{estimate.num_hands.join(" / ")}</td></tr>
              <tr><td>est. VRAM</td><td className="mono">{(estimate.estimated_vram_bytes / 1024 ** 3).toFixed(2)} GiB</td></tr>
              <tr><td>predicted peak</td>
                <td className="mono">
                  {(estimate.predicted_vram_bytes / 1024 ** 3).toFixed(2)} GiB
                  {estimate.over_vram_budget && <span className="error"> over the {(estimate.vram_limit_bytes / 1024 ** 3).toFixed(1)} GiB budget</span>}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="row" style={{ marginTop: 6 }}>
        <button onClick={doEstimate} disabled={busy}>Estimate</button>
        <button onClick={doSolve} disabled={busy}>Solve</button>
        <button className="ghost" onClick={doSave} disabled={busy}>{id ? "Save changes" : "Save to library"}</button>
      </div>
    </div>
  );
}
