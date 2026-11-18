import { useEffect, useRef, useState } from "react";

export interface ProgressEvent {
  status: string;
  iteration: number | null;
  iterations_run: number | null;
  exploitability: number | null;
  exploitability_iteration: number | null;
}

export interface StreamState {
  /** Latest snapshot-authoritative state. */
  current: ProgressEvent | null;
  /** Progress events accepted since connect (monotone in iteration). */
  points: { iteration: number; exploitability: number | null }[];
  /** "connecting" | "open" | "done" | "gone" | "closed" */
  stream: string;
}

/**
 * Subscribe to /v1/solves/{id}/events with the browser's EventSource.
 *
 * Contract (per the API): event ids are opaque <boot>-<seq>; the browser
 * reconnects automatically and sends Last-Event-ID; a snapshot replaces all
 * client state; progress events below the current iteration are stale and
 * ignored; `done`/`gone` are terminal.
 */
export function useJobStream(jobId: string | null): StreamState {
  const [state, setState] = useState<StreamState>({ current: null, points: [], stream: "connecting" });
  const iterRef = useRef(-1);

  useEffect(() => {
    if (!jobId) return;
    iterRef.current = -1;
    setState({ current: null, points: [], stream: "connecting" });
    const source = new EventSource(`/v1/solves/${jobId}/events`);

    const apply = (payload: ProgressEvent, replace: boolean) => {
      const iter = payload.iteration ?? payload.iterations_run ?? 0;
      setState((prev) => {
        const base = replace ? [] : prev.points;
        const pts = payload.exploitability != null && payload.exploitability_iteration != null
          && !base.some((p) => p.iteration === payload.exploitability_iteration)
          ? [...base, { iteration: payload.exploitability_iteration, exploitability: payload.exploitability }]
          : base;
        return { current: payload, points: pts, stream: prev.stream === "connecting" ? "open" : prev.stream };
      });
      iterRef.current = Math.max(iterRef.current, iter);
    };

    source.addEventListener("snapshot", (e) => {
      setState((prev) => ({ ...prev, stream: "open" }));
      apply(JSON.parse((e as MessageEvent).data), true);
    });
    source.addEventListener("progress", (e) => {
      const payload = JSON.parse((e as MessageEvent).data) as ProgressEvent;
      const iter = payload.iteration ?? payload.iterations_run ?? 0;
      if (iter < iterRef.current) return; // stale replay
      apply(payload, false);
    });
    source.addEventListener("done", (e) => {
      apply(JSON.parse((e as MessageEvent).data), false);
      setState((prev) => ({ ...prev, stream: "done" }));
      source.close();
    });
    source.addEventListener("gone", () => {
      setState((prev) => ({ ...prev, stream: "gone" }));
      source.close();
    });
    source.onerror = () => {
      // EventSource reconnects itself; only report the transient state.
      setState((prev) => (prev.stream === "done" || prev.stream === "gone" ? prev : { ...prev, stream: "connecting" }));
    };
    return () => source.close();
  }, [jobId]);

  return state;
}
