// 로그인·로그아웃 (ADR 0018 §1). 토큰은 httpOnly 쿠키에만 심고 응답 본문에는 싣지 않는다.
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, apiBase, errorBody, sameOrigin, sessionCookie } from "@/lib/server/config";

export const dynamic = "force-dynamic";

const noStore = { "cache-control": "no-store" };

export async function POST(req: NextRequest) {
  if (!sameOrigin(req.headers.get("origin"), req.headers.get("host"))) {
    return NextResponse.json(errorBody("FORBIDDEN", "다른 출처의 요청"), { status: 403, headers: noStore });
  }
  let password = "";
  try {
    const body = (await req.json()) as { password?: unknown };
    if (typeof body.password === "string") password = body.password;
  } catch {
    // 아래에서 400
  }
  if (!password) {
    return NextResponse.json(errorBody("INVALID_PARAM", "비밀번호를 입력하세요"), { status: 400, headers: noStore });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase()}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(errorBody("API_UNREACHABLE", "API 서버에 연결할 수 없습니다"), {
      status: 502,
      headers: noStore,
    });
  }
  const data = (await upstream.json().catch(() => null)) as
    | { token?: string; expires_at?: string; error?: unknown }
    | null;
  if (!upstream.ok || !data?.token || !data.expires_at) {
    const status = upstream.ok ? 502 : upstream.status;
    const body = data && data.error ? { error: data.error } : errorBody("LOGIN_FAILED", "로그인 실패");
    return NextResponse.json(body, { status, headers: noStore });
  }
  const res = NextResponse.json({ ok: true, expires_at: data.expires_at }, { headers: noStore });
  res.cookies.set(SESSION_COOKIE, data.token, sessionCookie(new Date(data.expires_at)));
  return res;
}

export async function DELETE(req: NextRequest) {
  if (!sameOrigin(req.headers.get("origin"), req.headers.get("host"))) {
    return NextResponse.json(errorBody("FORBIDDEN", "다른 출처의 요청"), { status: 403, headers: noStore });
  }
  const res = NextResponse.json({ ok: true }, { headers: noStore });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
