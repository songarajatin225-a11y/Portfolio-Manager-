/**
 * Browser API client. All requests go to this app's own `/api/v1/*` proxy so the httpOnly auth
 * cookies stay first-party. State-changing requests carry the double-submit CSRF token. A 401 with
 * `token_expired` triggers one silent refresh and a retry.
 */

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  requestId?: string;

  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const API_BASE = "/api/v1";
// Endpoints whose 401 means "bad credentials", not "access token expired".
const NO_REFRESH_PATHS = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout", "/auth/token"];
let refreshPromise: Promise<boolean> | null = null;

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=")[1]) : "";
}

async function parseError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {};
  try {
    body = await res.json();
  } catch {
    /* non-JSON error */
  }
  const err = body.error ?? {};
  return new ApiError(res.status, err.code ?? "http_error", err.message ?? res.statusText, err.details, err.request_id);
}

async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshPromise = null), 0);
      });
  }
  return refreshPromise;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  raw?: boolean;
}

export async function apiRequest<T = unknown>(path: string, opts: RequestOptions = {}, retry = true): Promise<T> {
  const method = (opts.method ?? (opts.body || opts.form ? "POST" : "GET")).toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = readCookie("mi_csrf");
  }
  const res = await fetch(`${API_BASE}${path}`, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  if (res.status === 401 && retry && !NO_REFRESH_PATHS.some((p) => path.startsWith(p))) {
    if (await refreshSession()) return apiRequest<T>(path, opts, false);
  }
  if (!res.ok) throw await parseError(res);
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PUT", body: body ?? {} }),
  patch: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PATCH", body: body ?? {} }),
  del: <T>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => apiRequest<T>(path, { method: "POST", form }),
};

/** SWR fetcher. */
export const fetcher = <T>(path: string) => api.get<T>(path);

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}
