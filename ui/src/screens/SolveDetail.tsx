import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useJobStream } from "../api/sse";
import ExploitabilityChart from "../components/ExploitabilityChart";
import StrategyGrid from "../components/StrategyGrid";

function fmt(n: number | null | undefined, digits = 3): string {
  if (n == null) return "—";
  return Math.abs(n) < 0.01 && n !== 0 ? n.toExponential(2) : n.toFixed(digits);
}

function Breadcrumbs({ history, onNav }: { history: string[]; onNav: (h: string[]) => void }) {
  return (
    <nav className="breadcrumbs" aria-label="node path">
      <button className={history.length === 0 ? "current" : ""} onClick={() => onNav([])}>root</button>
      {history.map((edge, i) => (
        <span key={i}>
          <span className="sep">/</span>
          <button
            className={i === history.length - 1 ? "current" : ""}
            onClick={() => onNav(history.slice(0, i + 1))}
          >
            {edge}
          </button>
        </span>
      ))}
    </nav>
  );
}

export default function SolveDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [history, setHistory] = useState<string[]>([]);
  const [combos, setCombos] = useState(false);
  const [selClass, setSelClass] = useState<string | null>(null);
  const stream = useJobStream(id);
  const done = stream.current?.status === "done";

  const statusQ = useQuery({
    queryKey: ["solve", id],
    queryFn: () => api.getSolve(id),
    refetchInterval: stream.stream === "done" ? false : 2000,
  });
  // When the stream reports done, refresh the final status once (refetchInterval stops).
  useEffect(() => {
    if (stream.stream === "done") qc.invalidateQueries({ queryKey: ["solve", id] });
  }, [stream.stream, id, qc]);
  const treeQ = useQuery({
    queryKey: ["tree", id, history],
    queryFn: () => api.tree(id, history),
    enabled: done || statusQ.data?.status === "done",
    retry: false,
  });
  const gridQ = useQuery({
    queryKey: ["handgrid", id, history],
    queryFn: () => api.handGrid(id, history),
    enabled: done || statusQ.data?.status === "done",
    retry: false,
  });
  const nodeQ = useQuery({
    queryKey: ["node", id, history, combos],
    queryFn: () => api.node(id, history, combos),
    enabled: (done || statusQ.data?.status === "done") && treeQ.data?.is_chance === false,
    retry: false,
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelSolve(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["solve", id] }),
  });
  const pin = useMutation({
    mutationFn: (pinned: boolean) => api.setPinned(id, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["solve", id] }),
  });

  if (statusQ.isLoading) return <p className="status">Loading…</p>;
  if (statusQ.error)
    return <p className="error" role="alert">{statusQ.error instanceof ApiError ? statusQ.error.message : String(statusQ.error)}</p>;
  const job = statusQ.data!;
  const cur = stream.current;
  const isDone = done || job.status === "done";
  // Chart = recorded history seeded from GET /v1/solves/{id}, then live SSE
  // points appended; an SSE point at an already-recorded iteration is dropped.
  const seen = new Set<number>();
  const chartPoints = [...(job.exploitability_history ?? []), ...stream.points]
    .filter((p) => p.exploitability != null)
    .filter((p) => !seen.has(p.iteration) && seen.add(p.iteration))
    .map((p) => ({ iteration: p.iteration, exploitability: p.exploitability }));

  return (
    <div>
      <h1>Job <span className="mono">{id}</span> <span className={`badge ${job.status}`}>{job.status}</span>
        {job.pinned && <span className="pin"> pinned</span>}</h1>

      <div className="panel">
        <div className="row">
          <div><label>status</label><span data-testid="job-status">{cur?.status ?? job.status}{stream.stream === "connecting" ? " (reconnecting)" : ""}</span></div>
          <div><label>gpu</label><span>{job.gpu ?? "—"}</span></div>
          <div><label>iteration</label><span className="mono">{cur?.iteration ?? job.iteration ?? "—"}</span></div>
          <div><label>exploitability</label><span className="mono" data-testid="job-expl">{cur?.exploitability != null ? cur.exploitability.toExponential(3) : job.exploitability != null ? job.exploitability.toExponential(3) : "—"}</span></div>
          <div><label>elapsed</label><span className="mono">{job.elapsed_seconds != null ? `${job.elapsed_seconds.toFixed(1)}s` : "—"}</span></div>
          <div><label>nodes</label><span className="mono">{job.num_nodes ?? "—"}</span></div>
          <div className="grow" />
          {["queued", "building", "solving", "exporting"].includes(job.status) && (
            <button className="danger" onClick={() => cancel.mutate()}>Cancel</button>
          )}
          {job.status === "done" && (
            <button className="ghost" onClick={() => pin.mutate(!job.pinned)}>{job.pinned ? "Unpin strategy" : "Pin strategy"}</button>
          )}
        </div>
        <h3>Exploitability (live)</h3>
        <ExploitabilityChart points={chartPoints} />
      </div>

      {isDone && job.strategy_available === false && !job.strategy_expired_at && (
        <p className="status">No strategy was exported for this job.</p>
      )}
      {job.strategy_expired_at != null && (
        <p className="status">The strategy was deleted by retention{job.pinned ? "" : " (pin solves to keep them)"}.</p>
      )}

      {isDone && job.strategy_available && (
        <>
          <Breadcrumbs history={history} onNav={(h) => { setHistory(h); setSelClass(null); }} />
          <div className="cols">
            <div className="panel">
              <h3>Tree node</h3>
              {treeQ.isLoading && <p className="status">Loading node…</p>}
              {treeQ.error && <p className="error" role="alert">{treeQ.error instanceof ApiError ? treeQ.error.message : String(treeQ.error)}</p>}
              {treeQ.data && (
                <div data-testid="tree-node">
                  <div className="row">
                    <div><label>street</label>{treeQ.data.street}</div>
                    <div><label>pot</label><span className="mono">{fmt(treeQ.data.pot, 1)}</span></div>
                    <div><label>stacks</label><span className="mono">p0 {fmt(treeQ.data.stacks.p0, 1)} / p1 {fmt(treeQ.data.stacks.p1, 1)}</span></div>
                    <div><label>board</label><span className="mono">{treeQ.data.board.join(" ")}</span></div>
                  </div>
                  {treeQ.data.is_chance ? (
                    <div>
                      <label>Chance node — dealt cards</label>
                      <div className="row" data-testid="dealt-cards">
                        {(treeQ.data.dealt_cards ?? []).map((c) => (
                          <button key={c} className="ghost" onClick={() => { setHistory([...history, `river:${c}`]); setSelClass(null); }}>{c}</button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div>
                      <label>player {treeQ.data.player} to act — children</label>
                      <div className="row" data-testid="children">
                        {(treeQ.data.children ?? []).map((ch) => (
                          <button
                            key={ch.edge}
                            className={ch.kind === "terminal" ? "ghost" : ""}
                            disabled={ch.kind === "terminal"}
                            title={ch.kind === "terminal" ? `terminal (${ch.outcome})` : ch.kind}
                            onClick={() => ch.history && setHistory(ch.history)}
                          >
                            {ch.edge}
                            {ch.kind === "terminal" ? ` (${ch.outcome})` : ch.kind === "chance" ? " → chance" : ` → p${ch.player}`}
                          </button>
                        ))}
                        {(treeQ.data.children ?? []).length === 0 && <span className="muted">terminal</span>}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="panel">
              <h3>Strategy at this node</h3>
              {gridQ.isLoading && <p className="status">Loading grid…</p>}
              {gridQ.error && <p className="error" role="alert">{gridQ.error instanceof ApiError ? gridQ.error.message : String(gridQ.error)}</p>}
              {gridQ.data && gridQ.data.nodes.length === 0 && <p className="status">No decision point here.</p>}
              {gridQ.data && gridQ.data.nodes.length > 0 && (
                (() => {
                  const node = gridQ.data.nodes.find((n) => n.history.join(",") === history.join(",")) ?? gridQ.data.nodes[0]!;
                  return (
                    <StrategyGrid
                      actions={node.actions}
                      cells={node.grid}
                      onSelect={(cls) => setSelClass(cls === selClass ? null : cls)}
                      selected={selClass}
                    />
                  );
                })()
              )}
            </div>
          </div>

          <div className="panel">
            <div className="row">
              <h3 className="grow">Hand detail {selClass ? `— ${selClass}` : ""}</h3>
              <label htmlFor="combos" style={{ display: "inline" }}>
                <input id="combos" type="checkbox" checked={combos} onChange={(e) => setCombos(e.target.checked)} /> per combo
              </label>
            </div>
            {nodeQ.isLoading && <p className="status">Loading…</p>}
            {nodeQ.error && <p className="error" role="alert">{nodeQ.error instanceof ApiError ? nodeQ.error.message : String(nodeQ.error)}</p>}
            {nodeQ.data && (
              <>
                <div className="row" data-testid="reaches">
                  <div className="grow">
                    <label>player 0 reach</label>
                    <div className="mono" style={{ maxHeight: 120, overflow: "auto" }}>
                      {(nodeQ.data.ranges.p0 ?? []).slice(0, 20).map((r) => `${r.hand_class ?? r.combo} ${((r.share ?? 0) * 100).toFixed(1)}%`).join("  ")}
                    </div>
                  </div>
                  <div className="grow">
                    <label>player 1 reach</label>
                    <div className="mono" style={{ maxHeight: 120, overflow: "auto" }}>
                      {(nodeQ.data.ranges.p1 ?? []).slice(0, 20).map((r) => `${r.hand_class ?? r.combo} ${((r.share ?? 0) * 100).toFixed(1)}%`).join("  ")}
                    </div>
                  </div>
                </div>
                <table data-testid="hands-table">
                  <thead>
                    <tr>
                      <th>{combos ? "combo" : "class"}</th><th>reach</th><th>weight</th>
                      {nodeQ.data.actions.map((a) => <th key={a.name}>{a.label}</th>)}
                      {nodeQ.data.actions.map((a) => <th key={a.name + "-ev"}>{a.label} EV</th>)}
                      <th>hand EV</th><th>equity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodeQ.data.hands
                      .filter((h) => !selClass || h.hand_class === selClass)
                      .map((h) => (
                        <tr key={h.combo ?? h.hand_class ?? "?"} data-cls={h.hand_class}>
                          <td className="mono">{h.combo ?? h.hand_class}</td>
                          <td className="mono">{fmt(h.reach)}</td>
                          <td className="mono">{fmt(h.weight)}</td>
                          {nodeQ.data.actions.map((a) => (
                            <td key={a.name} className="mono">{fmt(h.frequencies[a.name])}</td>
                          ))}
                          {nodeQ.data.actions.map((a) => (
                            <td key={a.name + "-ev"} className="mono">{fmt(h.action_evs[a.name], 2)}</td>
                          ))}
                          <td className="mono">{fmt(h.ev, 2)}</td>
                          <td className="mono">{h.equity != null ? `${(h.equity * 100).toFixed(1)}%` : "—"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
