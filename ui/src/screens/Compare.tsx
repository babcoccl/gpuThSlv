import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import StrategyGrid from "../components/StrategyGrid";

/** Compare two solves' strategies at the same node history. */
export default function Compare() {
  const [idA, setIdA] = useState("");
  const [idB, setIdB] = useState("");
  const [histText, setHistText] = useState("");

  const history = histText.split(",").map((s) => s.trim()).filter(Boolean);

  const jobsQ = useQuery({ queryKey: ["solves"], queryFn: () => api.listSolves("done") });
  const gridA = useQuery({
    queryKey: ["cmp", idA, histText],
    queryFn: () => api.handGrid(idA, history),
    enabled: !!idA,
    retry: false,
  });
  const gridB = useQuery({
    queryKey: ["cmp", idB, histText],
    queryFn: () => api.handGrid(idB, history),
    enabled: !!idB,
    retry: false,
  });

  const node = (g?: typeof gridA.data) =>
    g?.nodes.find((n) => n.history.join(",") === history.join(",")) ?? g?.nodes[0];

  return (
    <div>
      <h1>Compare solves</h1>
      <div className="panel">
        <div className="row">
          <div>
            <label htmlFor="job-a">solve A</label>
            <select id="job-a" value={idA} onChange={(e) => setIdA(e.target.value)} data-testid="job-a">
              <option value="">—</option>
              {jobsQ.data?.map((j) => <option key={j.id} value={j.id}>{j.id} {j.spot_name ? `(${j.spot_name})` : ""}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="job-b">solve B</label>
            <select id="job-b" value={idB} onChange={(e) => setIdB(e.target.value)} data-testid="job-b">
              <option value="">—</option>
              {jobsQ.data?.map((j) => <option key={j.id} value={j.id}>{j.id} {j.spot_name ? `(${j.spot_name})` : ""}</option>)}
            </select>
          </div>
          <div className="grow">
            <label htmlFor="hist">history (comma-separated, empty = root)</label>
            <input id="hist" type="text" className="mono" value={histText} onChange={(e) => setHistText(e.target.value)} placeholder="check,b10" />
          </div>
        </div>
      </div>
      <div className="cols">
        {[
          { id: idA, q: gridA, tag: "A" },
          { id: idB, q: gridB, tag: "B" },
        ].map(({ id, q, tag }) => (
          <div className="panel" key={tag} data-testid={`cmp-${tag}`}>
            <h3>{tag}: <span className="mono">{id || "—"}</span></h3>
            {id === "" && <p className="status">pick a solve</p>}
            {q.isLoading && <p className="status">Loading…</p>}
            {q.error && <p className="error" role="alert">{q.error instanceof ApiError ? q.error.message : String(q.error)}</p>}
            {q.data && node(q.data) && (
              <StrategyGrid actions={node(q.data)!.actions} cells={node(q.data)!.grid} />
            )}
            {q.data && q.data.nodes.length === 0 && <p className="status">no decision point at this history</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
