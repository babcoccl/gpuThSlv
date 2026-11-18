import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type SolveStatus = components["schemas"]["SolveStatus"];
export type JobStatus = SolveStatus["status"];
export type SpotRecord = components["schemas"]["SpotRecord"];
export type SpotSpec = components["schemas"]["SpotSpec"];
export type SolveParams = components["schemas"]["SolveParams"];
export type SolveRequest = components["schemas"]["SolveRequest"];
export type SpotCreate = components["schemas"]["SpotCreate"];
export type SpotUpdate = components["schemas"]["SpotUpdate"];
export type TreeNodeResponse = components["schemas"]["TreeNodeResponse"];
export type NodeValuesResponse = components["schemas"]["NodeValuesResponse"];
export type NodeHandRow = components["schemas"]["NodeHandRow"];
export type HandGridResponse = components["schemas"]["HandGridResponse"];
export type SolveAccepted = components["schemas"]["SolveAccepted"];
export type EstimateResponse = components["schemas"]["EstimateResponse"];
export type GpuInfo = components["schemas"]["GpuInfo"];
export type ExploitabilityPoint = components["schemas"]["ExploitabilityPoint"];

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

// Every UI API call goes through this generated client (paths, params and
// body shapes come from the OpenAPI schema). Middleware carries the auth
// contract: the CSRF header on mutating requests and ApiError on failures.
const gen = createClient<paths>({ baseUrl: "/" });
gen.use({
  onRequest({ request }) {
    const method = request.method.toUpperCase();
    if (method === "GET" || method === "HEAD") return;
    const headers = new Headers(request.headers);
    const token = csrfToken();
    if (token) headers.set(CSRF_HEADER, token);
    return new Request(request, { headers });
  },
  async onResponse({ response }) {
    if (response.ok) return;
    let detail: unknown;
    try {
      detail = (await response.clone().json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  },
});

/** A response body the contract guarantees; an empty one is a defect. */
function need<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new ApiError(500, `empty response: ${what}`);
  return data;
}

export const api = {
  login: async (key: string) => {
    const { data } = await gen.POST("/v1/auth/login", { body: { key } });
    return need(data, "login");
  },
  logout: () => gen.POST("/v1/auth/logout").then((r) => r.data),

  listSolves: async (status?: JobStatus) => {
    const { data } = await gen.GET("/v1/solves", {
      params: { query: status ? { status } : {} },
    });
    return data ?? [];
  },
  getSolve: async (id: string) => {
    const { data } = await gen.GET("/v1/solves/{job_id}", {
      params: { path: { job_id: id } },
    });
    return need(data, `solve ${id}`);
  },
  cancelSolve: async (id: string) => {
    const { data } = await gen.DELETE("/v1/solves/{job_id}", {
      params: { path: { job_id: id } },
    });
    return need(data, `cancel ${id}`);
  },
  setPinned: async (id: string, pinned: boolean) => {
    const { data } = pinned
      ? await gen.POST("/v1/solves/{job_id}/pin", { params: { path: { job_id: id } } })
      : await gen.POST("/v1/solves/{job_id}/unpin", { params: { path: { job_id: id } } });
    return need(data, `pin ${id}`);
  },

  submitSolve: async (body: SolveRequest) => {
    const { data } = await gen.POST("/v1/solves", { body });
    return need(data, "submit solve");
  },
  estimate: async (spot: SpotSpec, dtype: "float32" | "float64") => {
    const { data } = await gen.POST("/v1/estimate", { body: { spot, dtype } });
    return need(data, "estimate");
  },

  listSpots: async (tag?: string) => {
    const { data } = await gen.GET("/v1/spots", {
      params: { query: tag ? { tag } : {} },
    });
    return data ?? [];
  },
  getSpot: async (id: string) => {
    const { data } = await gen.GET("/v1/spots/{spot_id}", {
      params: { path: { spot_id: id } },
    });
    return need(data, `spot ${id}`);
  },
  createSpot: async (body: SpotCreate) => {
    const { data } = await gen.POST("/v1/spots", { body });
    return need(data, "create spot");
  },
  updateSpot: async (id: string, body: SpotUpdate) => {
    const { data } = await gen.PATCH("/v1/spots/{spot_id}", {
      params: { path: { spot_id: id } },
      body,
    });
    return need(data, `update spot ${id}`);
  },
  deleteSpot: async (id: string) => {
    const { data } = await gen.DELETE("/v1/spots/{spot_id}", {
      params: { path: { spot_id: id } },
    });
    return need(data, `delete spot ${id}`);
  },
  duplicateSpot: async (id: string, name?: string) => {
    const { data } = await gen.POST("/v1/spots/{spot_id}/duplicate", {
      params: { path: { spot_id: id }, query: name ? { name } : {} },
    });
    return need(data, `duplicate spot ${id}`);
  },
  solveSpot: async (id: string, params: SolveParams) => {
    const { data } = await gen.POST("/v1/spots/{spot_id}/solve", {
      params: { path: { spot_id: id } },
      body: params,
    });
    return need(data, `solve spot ${id}`);
  },

  tree: async (jobId: string, history: string[]) => {
    const { data } = await gen.GET("/v1/solves/{job_id}/tree", {
      params: { path: { job_id: jobId }, query: { history: history.join(",") } },
    });
    return need(data, `tree ${jobId}`);
  },
  node: async (jobId: string, history: string[], combos = false) => {
    const { data } = await gen.GET("/v1/solves/{job_id}/node", {
      params: {
        path: { job_id: jobId },
        query: { history: history.join(","), combos },
      },
    });
    return need(data, `node ${jobId}`);
  },
  handGrid: async (jobId: string, history: string[]) => {
    const { data } = await gen.GET("/v1/solves/{job_id}/strategy", {
      params: {
        path: { job_id: jobId },
        query: { view: "hand_grid", history: history.join(",") },
      },
    });
    const body = need(data, `hand grid ${jobId}`);
    if (body.view !== "hand_grid") throw new ApiError(500, `unexpected view ${body.view}`);
    return body;
  },
  listGpus: async () => {
    const { data } = await gen.GET("/v1/gpus");
    return data ?? [];
  },
};

export function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}
