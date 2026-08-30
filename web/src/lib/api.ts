/**
 * Typed fetch wrapper for the Plumbline API.
 *
 * - Every request sends cookies (`credentials: 'include'`) so the session
 *   cookie set by the backend rides along automatically.
 * - Every non-2xx response is turned into an `ApiError` here, in one place,
 *   rather than leaving callers to inspect a bare `Response`.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function messageFor(status: number, body: unknown): string {
  if (body && typeof body === "object" && "message" in body) {
    const m = (body as { message?: unknown }).message;
    if (typeof m === "string" && m.length > 0) return m;
  }
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail?: unknown }).detail;
    if (typeof d === "string" && d.length > 0) return d;
  }
  if (typeof body === "string" && body.length > 0) return body;
  return `Request failed with status ${status}`;
}

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

async function request<T>(path: string, init: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = init;
  const hasJsonBody = body !== undefined && !(body instanceof FormData);

  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(hasJsonBody ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: hasJsonBody ? JSON.stringify(body) : (body as BodyInit | undefined),
  });

  const parsed = await parseBody(res);

  if (!res.ok) {
    throw new ApiError(res.status, messageFor(res.status, parsed), parsed);
  }

  return parsed as T;
}

export const api = {
  get: <T>(path: string, init?: Omit<RequestInit, "body">) =>
    request<T>(path, { ...init, method: "GET" }),

  post: <T>(path: string, body?: unknown, init?: Omit<RequestInit, "body">) =>
    request<T>(path, { ...init, method: "POST", body }),

  patch: <T>(path: string, body?: unknown, init?: Omit<RequestInit, "body">) =>
    request<T>(path, { ...init, method: "PATCH", body }),

  put: <T>(path: string, body?: unknown, init?: Omit<RequestInit, "body">) =>
    request<T>(path, { ...init, method: "PUT", body }),

  del: <T>(path: string, init?: Omit<RequestInit, "body">) =>
    request<T>(path, { ...init, method: "DELETE" }),
};
