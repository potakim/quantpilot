// WS 인증 토큰 (ADR 0018 §1). 같은 출처에서만 읽히고, 클라이언트는 메모리에만 둔다.
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, errorBody, wsUrl } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  const headers = { "cache-control": "no-store" };
  if (!token) return NextResponse.json(errorBody("UNAUTHORIZED", "로그인이 필요합니다"), { status: 401, headers });
  return NextResponse.json({ token, url: wsUrl() }, { headers });
}
