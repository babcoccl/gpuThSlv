import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type SolveStatus = components["schemas"]["SolveStatus"];
export type SpotRecord = components["schemas"]["SpotRecord"];
export type TreeNodeResponse = components["schemas"]["TreeNodeResponse"];
export type NodeValuesResponse = components["schemas"]["NodeValuesResponse"];
export type NodeHandRow = components["schemas"]["NodeHandRow"];
export type HandGridResponse = components["schemas"]["HandGridResponse"];
export type SolveAccepted = components["schemas"]["SolveAccepted"];
export type EstimateResponse = components["schemas"]["EstimateResponse"];
export type GpuInfo = components["schemas"]["GpuInfo"];

const CSRF_COOKIE = "holdem_csrf";
const CSRF_HEADER = "x-csrf-token";

export function csrfToken(): string | null {
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${CSRF_COOKIE}=`));
  return match ? decodeURIComponent(match.slice(CSRF_COOKIE.length + 1)) : null;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

/** Wrap a fetch: sends the CSRF cookie value on mutating requests and turns
 *  error statuses into ApiError (401 handled by the caller). */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    const token = csrfToken();
    if (token) headers.set(CSRF_HEADER, token);
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = (await resp.json()).detail;
    } catch {
      detail = resp.statusText;
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// The generated client (paths + body shapes come from the OpenAPI schema);
// request() above only adds the CSRF header and error shaping.
const gen = createClient<paths>({ baseUrl: "/" });

export const api = {
  login: (key: string) => request<{ expires_at: number; csrf: string }>(
    "/v1/auth/login", { method: "POST", body: JSON.stringify({ key }) },
  ),
  logout: () => request<{ status: string }>("/v1/auth/logout", { method: "POST" }),

  listSolves: async (status?: string) => {
    const { data, error, response } = await gen.GET("/v1/solves", {
      params: { query: status ? { status: status as never } : {} },
    });
    if (error) throw new ApiError(response.status, error);
    return data ?? [];
  },
  getSolve: async (id: string) => {
    const { data, error, response } = await gen.GET("/v1/solves/{job_id}", {
      params: { path: { job_id: id } },
    });
    if (error) throw new ApiError(response.status, error);
    if (!data) throw new ApiError(500, "empty response");
    return data;
  },
  cancelSolve: (id: string) =>
    request<SolveStatus>(`/v1/solves/${id}`, { method: "DELETE" }),
  setPinned: (id: string, pinned: boolean) =>
    request<{ id: string; pinned: boolean }>(
      `/v1/solves/${id}/${pinned ? "pin" : "unpin"}`, { method: "POST" },
    ),

  submitSolve: (body: Record<string, unknown>) =>
    request<SolveAccepted>("/v1/solves", { method: "POST", body: JSON.stringify(body) }),
  estimate: (spot: Record<string, unknown>, dtype: string) =>
    request<EstimateResponse>("/v1/estimate", {
      method: "POST", body: JSON.stringify({ spot, dtype }),
    }),

  listSpots: async (tag?: string) => {
    const { data, error, response } = await gen.GET("/v1/spots", {
      params: { query: tag ? { tag } : {} },
    });
    if (error) throw new ApiError(response.status, error);
    return data ?? [];
  },
  getSpot: (id: string) => request<SpotRecord>(`/v1/spots/${id}`),
  createSpot: (body: { name: string; spot: Record<string, unknown>; tags: string[]; notes?: string }) =>
    request<SpotRecord>("/v1/spots", { method: "POST", body: JSON.stringify(body) }),
  updateSpot: (id: string, body: Record<string, unknown>) =>
    request<SpotRecord>(`/v1/spots/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSpot: (id: string) => request<{ status: string }>(`/v1/spots/${id}`, { method: "DELETE" }),
  duplicateSpot: (id: string, name?: string) =>
    request<SpotRecord>(`/v1/spots/${id}/duplicate${name ? `?name=${encodeURIComponent(name)}` : ""}`, {
      method: "POST",
    }),
  solveSpot: (id: string, params: Record<string, unknown>) =>
    request<SolveAccepted>(`/v1/spots/${id}/solve`, { method: "POST", body: JSON.stringify(params) }),

  tree: (jobId: string, history: string[]) =>
    request<TreeNodeResponse>(
      `/v1/solves/${jobId}/tree?history=${encodeURIComponent(history.join(","))}`,
    ),
  node: (jobId: string, history: string[], combos = false) =>
    request<NodeValuesResponse>(
      `/v1/solves/${jobId}/node?history=${encodeURIComponent(history.join(","))}&combos=${combos}`,
    ),
  handGrid: (jobId: string, history: string[]) =>
    request<HandGridResponse>(
      `/v1/solves/${jobId}/strategy?view=hand_grid&history=${encodeURIComponent(history.join(","))}`,
    ),
  listGpus: () => request<GpuInfo[]>("/v1/gpus"),
};

export function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}
