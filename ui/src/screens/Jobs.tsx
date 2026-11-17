import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, SolveStatus } from "../api/client";

function fmtElapsed(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export default function Jobs() {
  const qc = useQueryClient();
  const { data: jobs, isLoading, error } = useQuery({
    queryKey: ["solves"],
    queryFn: () => api.listSolves(),
    refetchInterval: 3000,
  });
  const { data: gpus } = useQuery({ queryKey: ["gpus"], queryFn: api.listGpus });

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelSolve(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["solves"] }),
  });
  const pin = useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => api.setPinned(id, pinned),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["solves"] }),
  });

  if (isLoading) return <p className="status">Loading jobs…</p>;
  if (error) return <p className="error" role="alert">{String(error instanceof Error ? error.message : error)}</p>;

  const gpuName = (g: number | null | undefined) =>
    g == null ? "—" : `gpu${g}${gpus?.[g]?.device ? ` (${gpus[g].device})` : ""}`;

  return (
    <div>
      <h1>Jobs</h1>
      {!jobs?.length ? (
        <p className="status">No solves yet. <Link to="/spots/new">Build a spot</Link> or solve one from the <Link to="/spots">library</Link>.</p>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>id</th><th>spot</th><th>status</th><th>gpu</th><th>exploitability</th>
                <th>iterations</th><th>elapsed</th><th>dtype</th><th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j: SolveStatus) => (
                <tr key={j.id} className="clickable" data-testid={`job-${j.id}`}>
                  <td className="mono"><Link to={`/jobs/${j.id}`}>{j.id}</Link></td>
                  <td>{j.spot_name ?? "—"}</td>
                  <td><span className={`badge ${j.status}`}>{j.status}</span>{j.pinned && <span className="pin" title="pinned"> pinned</span>}</td>
                  <td>{gpuName(j.gpu)}</td>
                  <td className="mono">{j.exploitability != null ? j.exploitability.toExponential(2) : "—"}</td>
                  <td className="mono">{j.iterations_run ?? "—"}</td>
                  <td className="mono">{fmtElapsed(j.elapsed_seconds)}</td>
                  <td className="mono">{j.dtype}</td>
                  <td>
                    {["queued", "building", "solving"].includes(j.status) && (
                      <button className="danger small" onClick={() => cancel.mutate(j.id)}>Cancel</button>
                    )}
                    {j.status === "done" && (
                      <button className="ghost small" onClick={() => pin.mutate({ id: j.id, pinned: !j.pinned })}>
                        {j.pinned ? "Unpin" : "Pin"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
