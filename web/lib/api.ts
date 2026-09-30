// 브라우저 → 같은 출처 프록시(/api/qp/*) 호출 (ADR 0018 §1). 토큰은 httpOnly 쿠키에 있고
// 프록시가 서버 쪽에서 Authorization을 붙이므로 여기서는 토큰을 다루지 않는다.

export const PROXY_PREFIX = "/api/qp";

/** 03 §1 오류 형식 `{error:{code,message,detail}}`를 담는 예외. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly reason: string,
    public readonly detail: Record<string, unknown> = {},
  ) {
    super(reason);
    this.name = "ApiError";
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** 응답 본문 → ApiError. message가 비면 detail.risk.reason(RiskDecision)을 쓴다. */
export function parseError(status: number, body: unknown): ApiError {
  const err = isRecord(body) && isRecord(body.error) ? body.error : null;
  if (!err) return new ApiError(status, `HTTP_${status}`, `요청 실패 (HTTP ${status})`);
  const detail = isRecord(err.detail) ? err.detail : {};
  const risk = isRecord(detail.risk) ? detail.risk : null;
  const message =
    (typeof err.message === "string" && err.message) ||
    (risk && typeof risk.reason === "string" && risk.reason) ||
    `요청 실패 (HTTP ${status})`;
  const code = typeof err.code === "string" && err.code ? err.code : `HTTP_${status}`;
  return new ApiError(status, code, message, detail);
}

/** 화면에 보여 줄 사유 한 줄. */
export function reasonText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "RISK_REJECTED") return `리스크 게이트 거부 · ${e.reason}`;
    if (e.code === "HALTED") return `신규 진입 중단 · ${e.reason}`;
    return e.reason;
  }
  if (e instanceof Error) return e.message;
  return "알 수 없는 오류";
}

export interface ApiInit {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  json?: unknown;
  signal?: AbortSignal;
}

export interface ApiDeps {
  fetchImpl?: typeof fetch;
  onUnauthorized?: () => void;
}

let unauthorizedHandler: (() => void) | undefined;

/** 401을 받았을 때 할 일 (보통 /login으로 이동). */
export function setUnauthorizedHandler(fn: (() => void) | undefined): void {
  unauthorizedHandler = fn;
}

/** `/portfolio` 같은 API v1 경로를 프록시로 호출하고 JSON을 돌려준다. */
export async function apiFetch<T>(path: string, init: ApiInit = {}, deps: ApiDeps = {}): Promise<T> {
  if (!path.startsWith("/")) throw new Error("API 경로는 /로 시작해야 한다");
  const fetchImpl = deps.fetchImpl ?? fetch;
  const headers: Record<string, string> = { accept: "application/json" };
  const req: RequestInit = {
    method: init.method ?? "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers,
    signal: init.signal,
  };
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    req.body = JSON.stringify(init.json);
  }
  const res = await fetchImpl(`${PROXY_PREFIX}${path}`, req);
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401) (deps.onUnauthorized ?? unauthorizedHandler)?.();
    throw parseError(res.status, body);
  }
  return body as T;
}
