import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, SpotRecord, SpotUpdate } from "../api/client";

function SolveDialog({ spot, onClose }: { spot: SpotRecord; onClose: () => void }) {
  const nav = useNavigate();
  const [iterations, setIterations] = useState("1000");
  const [dtype, setDtype] = useState<"float32" | "float64">("float32");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const acc = await api.solveSpot(spot.id, { iterations: Number(iterations), dtype, priority: 0, variant: "cfr_plus" });
      nav(`/jobs/${acc.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <div className="panel" role="dialog" aria-label={`solve ${spot.name}`}>
      <h3>Solve {spot.name}</h3>
      <div className="row">
        <div><label htmlFor="sd-iters">iterations</label>
          <input id="sd-iters" type="number" min="1" value={iterations} onChange={(e) => setIterations(e.target.value)} /></div>
        <div><label htmlFor="sd-dtype">dtype</label>
          <select id="sd-dtype" value={dtype} onChange={(e) => setDtype(e.target.value as "float32" | "float64")}>
            <option value="float32">float32</option><option value="float64">float64</option>
          </select></div>
        <button onClick={submit} disabled={busy}>Solve</button>
        <button className="ghost" onClick={onClose}>Close</button>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
    </div>
  );
}

export default function Spots() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [tagFilter, setTagFilter] = useState("");
  const [solveFor, setSolveFor] = useState<SpotRecord | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editTags, setEditTags] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const { data: spots, isLoading, error } = useQuery({
    queryKey: ["spots", tagFilter],
    queryFn: () => api.listSpots(tagFilter || undefined),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["spots"] });
  const onErr = (e: unknown) => setErr(e instanceof ApiError ? e.message : String(e));

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: SpotUpdate }) => api.updateSpot(id, body),
    onSuccess: () => { invalidate(); setEditId(null); },
    onError: onErr,
  });
  const dup = useMutation({ mutationFn: (id: string) => api.duplicateSpot(id), onSuccess: invalidate, onError: onErr });
  const del = useMutation({ mutationFn: (id: string) => api.deleteSpot(id), onSuccess: invalidate, onError: onErr });

  if (isLoading) return <p className="status">Loading spots…</p>;
  if (error) return <p className="error" role="alert">{String(error instanceof Error ? error.message : error)}</p>;

  return (
    <div>
      <div className="row">
        <h1 className="grow">Spot library</h1>
        <input type="text" placeholder="filter by tag" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} aria-label="filter by tag" />
        <Link to="/spots/new"><button>New spot</button></Link>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {solveFor && <SolveDialog spot={solveFor} onClose={() => setSolveFor(null)} />}
      {!spots?.length ? (
        <p className="status">No saved spots. <Link to="/spots/new">Build one</Link>.</p>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr><th>name</th><th>street</th><th>board</th><th>tags</th><th>updated</th><th></th></tr>
            </thead>
            <tbody>
              {spots.map((s) => {
                const board = s.spot?.board;
                const boardText = Array.isArray(board) ? board.join(" ") : String(board ?? "");
                const street = board ? (Array.isArray(board) ? board.length : String(board).length / 2) === 4 ? "turn" : "river" : "—";
                return (
                  <tr key={s.id} data-testid={`spot-${s.id}`}>
                    <td>
                      {editId === s.id ? (
                        <input type="text" value={editName} onChange={(e) => setEditName(e.target.value)} aria-label="rename spot" />
                      ) : (
                        <Link to={`/spots/${s.id}/edit`}>{s.name}</Link>
                      )}
                    </td>
                    <td>{street}</td>
                    <td className="mono">{boardText}</td>
                    <td>
                      {editId === s.id ? (
                        <input type="text" value={editTags} onChange={(e) => setEditTags(e.target.value)} aria-label="edit tags" />
                      ) : (
                        s.tags.map((t) => <span key={t} className="tag-chip">{t}</span>)
                      )}
                    </td>
                    <td className="mono">{new Date(s.updated_at * 1000).toISOString().slice(0, 16).replace("T", " ")}</td>
                    <td>
                      {editId === s.id ? (
                        <>
                          <button className="small" onClick={() =>
                            update.mutate({ id: s.id, body: { name: editName, tags: editTags.split(",").map((t) => t.trim()).filter(Boolean) } })
                          }>Save</button>{" "}
                          <button className="ghost small" onClick={() => setEditId(null)}>Cancel</button>
                        </>
                      ) : (
                        <>
                          <button className="small" onClick={() => setSolveFor(s)}>Solve</button>{" "}
                          <button className="ghost small" onClick={() => nav(`/spots/${s.id}/edit`)}>Edit</button>{" "}
                          <button className="ghost small" onClick={() => { setEditId(s.id); setEditName(s.name); setEditTags(s.tags.join(", ")); }}>Rename</button>{" "}
                          <button className="ghost small" onClick={() => dup.mutate(s.id)}>Duplicate</button>{" "}
                          <button className="danger small" onClick={() => del.mutate(s.id)}>Delete</button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
