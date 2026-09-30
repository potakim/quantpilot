// 서버 전용 설정 (ADR 0018 §1). 라우트 핸들러·미들웨어만 import한다 — 클라이언트 번들에 들어가지 않는다.
// API 주소는 서버 환경변수 QP_API_URL, 브라우저가 붙을 WS 주소는 QP_WS_URL. 브라우저 공개용 환경변수는 쓰지 않는다.

export const SESSION_COOKIE = "qp_session";

/** API 서버 기준 주소 (끝 슬래시 없음). */
export function apiBase(): string {
  return (process.env.QP_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

/** 브라우저가 직접 붙는 WS 주소. 없으면 QP_API_URL을 ws(s)://…/api/v1/ws로 바꾼다. */
export function wsUrl(): string {
  const explicit = process.env.QP_WS_URL;
  if (explicit) return explicit;
  return `${apiBase().replace(/^http/, "ws")}/api/v1/ws`;
}

/** 세션 쿠키 속성: httpOnly · SameSite=Strict · 운영 빌드에서 Secure. */
export function sessionCookie(expires: Date) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires,
  };
}

/** 변경 요청의 Origin이 이 서버와 같은지 (없으면 같은 출처로 본다 — SameSite=Strict가 1차 방어). */
export function sameOrigin(origin: string | null, host: string | null): boolean {
  if (!origin) return true;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function errorBody(code: string, message: string) {
  return { error: { code, message, detail: {} } };
}
