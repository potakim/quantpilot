// 같은 출처 REST 프록시 (ADR 0018 §1): /api/qp/<경로> → QP_API_URL/api/v1/<경로>.
// 쿠키의 JWT를 서버 쪽에서 Authorization 헤더로 붙인다. 토큰·쿠키는 로그에 남기지 않는다.
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, apiBase, errorBody, sameOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

const noStore = { "cache-control": "no-store" };

async function handle(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  if (!path.length || path.some((s) => s === "." || s === ".." || s.includes("/") || s.includes("\\"))) {
    return NextResponse.json(errorBody("INVALID_PARAM", "잘못된 경로"), { status: 400, headers: noStore });
  }
  const mutating = req.method !== "GET";
  if (mutating && !sameOrigin(req.headers.get("origin"), req.headers.get("host"))) {
    return NextResponse.json(errorBody("FORBIDDEN", "다른 출처의 요청"), { status: 403, headers: noStore });
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  const headers: Record<string, string> = { accept: "application/json" };
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;
  if (token) headers.authorization = `Bearer ${token}`;
  const url = `${apiBase()}/api/v1/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: mutating ? await req.text() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return NextResponse.json(errorBody("API_UNREACHABLE", "API 서버에 연결할 수 없습니다"), {
      status: 502,
      headers: noStore,
    });
  }
  const res = new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", ...noStore },
  });
  if (upstream.status === 401 && token) res.cookies.delete(SESSION_COOKIE);
  return res;
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
