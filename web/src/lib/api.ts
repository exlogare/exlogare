
import i18n from "../i18n";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

type ApiOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  headers?: Record<string, string>;
};

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** Map backend English error details to i18n keys (shown in the active UI language). */
const API_ERROR_I18N: Array<{ match: string; key: string }> = [
  {
    match:
      "OAuth client_id required. Set GITFLIC_OAUTH_CLIENT_ID for the cloud instance or provide one per-connection for self-hosted.",
    key: "errors.oauth_client_id_gitflic",
  },
  {
    match: "OAuth client_id required for this GitLab instance",
    key: "errors.oauth_client_id_gitlab",
  },
  {
    match:
      "OAuth client_id required (platform env or per-tenant for GitHub Enterprise)",
    key: "errors.oauth_client_id_github",
  },
  {
    match:
      "OAuth client_id required (set BITBUCKET_OAUTH_CLIENT_ID or supply per-tenant)",
    key: "errors.oauth_client_id_bitbucket",
  },
];

function localizeApiError(message: string): string {
  for (const entry of API_ERROR_I18N) {
    if (message === entry.match && i18n.exists(entry.key)) {
      return i18n.t(entry.key);
    }
  }
  return message;
}

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

let csrfBootstrap: Promise<string | null> | null = null;

async function ensureCsrfToken(): Promise<string | null> {
  const existing = readCsrfCookie();
  if (existing) return existing;
  if (!csrfBootstrap) {
    csrfBootstrap = fetch("/api/auth/csrf", {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (res) => {
        if (!res.ok) return null;
        const data = (await res.json().catch(() => null)) as { csrf?: string } | null;
        return readCsrfCookie() ?? data?.csrf ?? null;
      })
      .catch(() => null)
      .finally(() => {
        csrfBootstrap = null;
      });
  }
  return csrfBootstrap;
}

export function clearCsrfCache(): void {
  csrfBootstrap = null;
}

/** Flatten FastAPI / Pydantic error shapes into a single human-readable line. */
function formatApiErrorMessage(detail: unknown, status: number): string {
  let message = `HTTP ${status}`;
  if (typeof detail === "string" && detail.length > 0) {
    message = detail;
  } else if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const obj = item as { loc?: unknown; msg?: unknown };
        const msg = typeof obj.msg === "string" ? obj.msg : null;
        if (!msg) return null;
        if (Array.isArray(obj.loc) && obj.loc.length > 1) {
          // Skip the first element ("body") — it's noise for users.
          const field = obj.loc.slice(1).join(".");
          return field ? `${field}: ${msg}` : msg;
        }
        return msg;
      })
      .filter((s): s is string => Boolean(s));
    if (parts.length > 0) message = parts.join("; ");
  }
  return localizeApiError(message);
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(opts.headers ?? {}),
  };
  if (!SAFE_METHODS.has(method)) {
    const token = await ensureCsrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    let detail: unknown = data;
    if (data && typeof data === "object" && "detail" in data) {
      detail = (data as { detail: unknown }).detail;
    }
    const message = formatApiErrorMessage(detail, res.status);
    throw new ApiError(res.status, detail, message);
  }
  return data as T;
}
