# 0018 · 웹 프론트엔드: JWT 쿠키·같은 출처 프록시, `web/` 전용 Node 도구, 데모 API로 접근성 측정

상태: 승인 (2026-09-30)

## 맥락
P1-13은 `web/`에 Next.js 화면(대시보드·거래·AI 판단 로그·모바일 대시보드)을 만드는 카드다. API(P1-12, ADR 0017)는 `Authorization: Bearer <JWT>`와 WS 첫 메시지 `{"auth": JWT}`로 인증한다. 브라우저가 이 토큰을 어디에 두느냐가 정해지지 않았다. `localStorage`에 두면 스크립트 주입 한 번으로 토큰이 새고, 불변식 #10(키·비밀이 로그·응답·화면에 남지 않는다)의 취지와도 어긋난다. 또 저장소는 지금까지 Python만 있었으므로 Node 도구를 어디에 두고 CI에서 어떻게 돌릴지, 로그인이 필요한 화면의 Lighthouse 접근성 90+(09 문서 P1-13 완료 기준)를 무엇을 상대로 재는지도 정해야 했다.

월 손실 게이지 색 규칙도 10 문서 두 곳(§2 주황 목록, §3 게이지)이 서로 달리 읽혔다. 운영자가 2026-09-30에 기준을 정했다.

## 결정
1. **JWT는 httpOnly 쿠키에만 둔다.**
   - 로그인은 Next.js 라우트 핸들러(`web/app/api/session/route.ts`)가 받는다. 서버 쪽에서 API `POST /auth/login`을 부르고, 받은 토큰을 `httpOnly`·`SameSite=Strict`·`Path=/` 쿠키로 심는다. 운영 빌드(`NODE_ENV=production`)에서는 `Secure`를 붙이고, 만료는 API의 `expires_at`을 따른다. 응답 본문에는 토큰을 싣지 않는다.
   - 브라우저의 REST 호출은 같은 출처 프록시 `web/app/api/qp/[...path]/route.ts`로 보낸다. 프록시가 쿠키에서 토큰을 꺼내 `Authorization: Bearer`를 서버 쪽에서 붙이고 `QP_API_URL`(기본 `http://127.0.0.1:8000`)의 `/api/v1/*`로 넘긴다. 쿠키·토큰은 로그에 남기지 않는다.
   - WS는 브라우저가 API에 직접 연결해야 하므로 첫 메시지에 토큰이 필요하다. 같은 출처 라우트 핸들러 `GET /api/session/ws-token`이 토큰과 WS 주소를 돌려주고, 클라이언트는 이를 JS 메모리(zustand 스토어 밖의 모듈 변수)에만 둔다. `localStorage`·`sessionStorage`·URL·쿼리 문자열·콘솔에는 쓰지 않는다.
   - 로그아웃(`DELETE /api/session`)은 쿠키를 지운다. 프록시가 401을 받으면 쿠키를 지우고 화면은 `/login`으로 보낸다.
   - API 주소는 서버 환경변수 `QP_API_URL`, 브라우저가 붙을 WS 주소는 `QP_WS_URL`(없으면 `QP_API_URL`을 `ws(s)://…/api/v1/ws`로 바꾼 값)에서만 온다. 비밀이 섞일 수 있는 값은 `NEXT_PUBLIC_*`로 내보내지 않는다.
   - 빌드 산출물(`web/.next/static`)과 소스에 `localStorage`·`sessionStorage` 토큰 사용, `QP_ADMIN_PASSWORD`·`QP_JWT_SECRET` 문자열, `Bearer ` 헤더 조립이 없는지 `web/scripts/check-secrets.mjs`가 검사한다.
2. **Node 도구는 `web/` 안에만 둔다.** `web/package.json`·`web/package-lock.json`이 전부이고 루트에는 Node 파일이 없다. 파이썬 코어의 의존성 규칙(CLAUDE.md)은 그대로다. CI에는 기존 `test` 잡을 건드리지 않고 `web` 잡을 따로 둔다: `npm ci` → lint → typecheck → test(vitest) → build → check-secrets → 데모 API와 `next start` 기동 → `lhci autorun`.
3. **Lighthouse는 합성 데이터를 채운 데모 API를 상대로 잰다.** `scripts/web_demo_api.py`가 `create_app`에 테스트와 같은 부품(SQLite 인메모리·MemoryHub·스텁 답변기)을 넣고 `data/synthetic.py`로 만든 캔들과 결정적인 판단·체결·포지션을 심어 `127.0.0.1:8000`에 띄운다. 데모 비밀번호·JWT 시크릿 기본값은 이 스크립트 안에만 있고 환경변수로 덮을 수 있다. 패키지·테스트는 이 스크립트를 import하지 않는다. 로그인이 필요한 화면은 lhci의 puppeteer 스크립트(`web/lighthouse/login.cjs`)가 `/login` 폼으로 로그인한 뒤 측정한다. 로컬 WSL에는 Chrome이 없으므로 측정은 CI에서만 한다.
4. **월 손실 게이지 색**(운영자 결정 2026-09-30): 사용률 = |월 손익| ÷ |월 한도|. 24% 이하 `--ok`, 24% 초과 60% 이하 `--warn`, 60% 초과 `--up`. 월 손익이 플러스면 사용률 0%. 표시는 `−1.2% / 한도 −5%`(음수는 U+2212). 10 문서 §2의 주황 목록에 적힌 "월 손실 게이지(60% 이상일 때)"는 사람이 들여다봐야 하는 시점을 말하고, 24~60% 주황 구간은 게이지의 표시 색 단계다. 계산은 `web/lib/gauge.ts` 순수 함수 하나에 두고 경계값(0, 24, 24.01, 60, 60.01, 100 이상, 플러스 손익)을 테스트한다.

## 결과
- 스크립트 주입이 있어도 REST 토큰은 읽히지 않는다. WS 토큰은 탭이 살아 있는 동안 메모리에 있으므로 그 범위의 노출은 남는다. 탭을 새로 열면 다시 받는다.
- 같은 출처 프록시 한 겹이 늘어 REST 호출마다 Next 서버를 한 번 더 거친다. 단일 사용자라 부하는 문제 되지 않는다.
- 화면은 API 응답의 숫자만 쓴다. API가 아직 주지 않는 값(전략별 이번 달 손익·MDD, 오늘 일정 표, 자산 곡선 시계열, 규칙 미충족 건수)은 지어내지 않고 "—" 또는 "데이터 없음"으로 둔다. 이 값들은 API 쪽 후속 카드가 채운다.
- 리스크 규칙은 설정 화면에서 읽기 전용(잠금 표시)으로만 보이고, 앱 어디에도 규칙을 바꾸는 입력이 없다(불변식 #6).
- 로컬 Lighthouse 측정값은 없다. CI `web` 잡의 lhci 결과가 완료 기준의 근거다.
