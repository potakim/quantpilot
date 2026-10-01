#!/usr/bin/env node
// 비밀 누출 검사 (ADR 0018 §1, 불변식 #10). 빌드 뒤 실행: npm run build && npm run check-secrets
//
// 1) 소스(app·components·lib·middleware.ts)
//    - localStorage·sessionStorage 사용 금지 (토큰은 httpOnly 쿠키, WS 토큰은 메모리)
//    - QP_ADMIN_PASSWORD·QP_JWT_SECRET 문자열 금지
//    - "Bearer " 헤더 조립은 서버 전용 경로(app/api/**, lib/server/**)에서만
//    - NEXT_PUBLIC_* 금지 (서버 환경변수만 쓴다)
// 2) 클라이언트 번들(.next/static)
//    - QP_ADMIN_PASSWORD·QP_JWT_SECRET·"Bearer "·세션 쿠키 이름(qp_session) 금지
//    - localStorage·sessionStorage 근처(±160자)에 token·auth·jwt·qp_ 가 있으면 실패
//      (Next 런타임 자체의 스크롤 복원용 sessionStorage는 토큰과 무관하므로 허용)
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const problems = [];

function walk(dir, exts, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, exts, out);
    else if (exts.some((e) => name.endsWith(e))) out.push(p);
  }
  return out;
}

const rel = (p) => relative(root, p).split(sep).join("/");

// ── 1. 소스 ──
const sources = [
  ...walk(join(root, "app"), [".ts", ".tsx"]),
  ...walk(join(root, "components"), [".ts", ".tsx"]),
  ...walk(join(root, "lib"), [".ts", ".tsx"]),
  join(root, "middleware.ts"),
].filter((p) => existsSync(p));

for (const file of sources) {
  const text = readFileSync(file, "utf8");
  const r = rel(file);
  const serverOnly = r.startsWith("app/api/") || r.startsWith("lib/server/");
  if (/\b(localStorage|sessionStorage)\b/.test(text)) problems.push(`${r}: localStorage/sessionStorage 사용`);
  if (/QP_ADMIN_PASSWORD|QP_JWT_SECRET/.test(text)) problems.push(`${r}: 비밀 환경변수 이름이 소스에 있음`);
  if (/NEXT_PUBLIC_/.test(text)) problems.push(`${r}: NEXT_PUBLIC_* 사용 (서버 환경변수만 쓴다)`);
  if (!serverOnly && /Bearer /.test(text)) problems.push(`${r}: 클라이언트 코드에서 Bearer 헤더 조립`);
}

// ── 2. 클라이언트 번들 ──
const staticDir = join(root, ".next", "static");
const sourceOnly = process.argv.includes("--source-only");
if (!existsSync(staticDir)) {
  if (!sourceOnly) problems.push(".next/static 없음 — 먼저 npm run build (소스만 보려면 --source-only)");
} else {
  const bundles = walk(staticDir, [".js"]);
  for (const file of bundles) {
    const text = readFileSync(file, "utf8");
    const r = rel(file);
    for (const needle of ["QP_ADMIN_PASSWORD", "QP_JWT_SECRET", "Bearer ", "qp_session"]) {
      if (text.includes(needle)) problems.push(`${r}: 번들에 "${needle}"`);
    }
    const re = /localStorage|sessionStorage/g;
    let m;
    while ((m = re.exec(text))) {
      const around = text.slice(Math.max(0, m.index - 160), m.index + 160);
      if (/token|jwt|auth|qp_/i.test(around)) {
        problems.push(`${r}: ${m[0]} 근처에 토큰 관련 식별자 (…${around.replace(/\s+/g, " ").slice(120, 220)}…)`);
        break;
      }
    }
  }
  console.log(`번들 ${bundles.length}개 검사`);
}
console.log(`소스 ${sources.length}개 검사`);

if (problems.length) {
  console.error(`비밀 누출 검사 실패 (${problems.length}건):`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log("비밀 누출 검사 통과");
