// 세션 쿠키가 없으면 /login으로 보낸다. 토큰 검증은 API가 한다 (만료면 프록시가 401 → 화면이 /login으로).
import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "qp_session";

export function middleware(req: NextRequest) {
  if (req.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const url = req.nextUrl.clone();
  const next = req.nextUrl.pathname;
  url.pathname = "/login";
  url.search = next && next !== "/" ? `?next=${encodeURIComponent(next)}` : "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!api/|_next/|login|favicon.ico|icon.svg).*)"],
};
