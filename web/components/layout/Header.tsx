"use client";

// 헤더 56px (docs/10 §3): 로고 · 시장 탭 · 모드 배지 · KST 시계 · 연결 상태 · 알림 · 사용자.
// 모드 배지와 연결 상태는 모든 화면에 항상 보인다. 값은 /health·/reports/gates·WS 상태에서 온다.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { IconBell, IconUser } from "@/components/ui/Icons";
import { cx } from "@/components/ui/primitives";
import { kstClock, kstRelTime } from "@/lib/format";
import { MARKET_TABS, useUi } from "@/lib/ui-store";
import { useGates, useHealth, useRiskEvents } from "@/lib/queries";
import { useLive } from "@/lib/ws";

export type ConnTone = "ok" | "warn" | "up";

/** 연결 상태 한 줄 (가장 나쁜 것 우선). */
export function useConnection(): { tone: ConnTone; text: string; engineDown: boolean; apiDown: boolean } {
  const health = useHealth();
  const live = useLive((s) => s.status);
  if (health.isError) return { tone: "up", text: "API 연결 끊김", engineDown: false, apiDown: true };
  const h = health.data;
  if (!h) return { tone: "warn", text: "연결 확인 중", engineDown: false, apiDown: false };
  if (!h.engine_alive) return { tone: "up", text: "엔진 응답 없음", engineDown: true, apiDown: false };
  const feeds = [h.ws_connected.upbit && "업비트", h.ws_connected.kis && "KIS"].filter(Boolean) as string[];
  if (live !== "open") return { tone: "warn", text: "실시간 재연결 중", engineDown: false, apiDown: false };
  if (!feeds.length) return { tone: "warn", text: "시세 수신 없음", engineDown: false, apiDown: false };
  return { tone: "ok", text: `${feeds.join(" · ")} 연결됨`, engineDown: false, apiDown: false };
}

const DOT: Record<ConnTone, string> = { ok: "bg-ok", warn: "bg-warn", up: "bg-up" };

function Clock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="num hidden min-w-[26ch] text-right text-[13px] text-muted lg:block" aria-label="현재 시각 (KST)">
      {now ? kstClock(now) : " "}
    </div>
  );
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const events = useRiskEvents(open);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const items = events.data?.items ?? [];
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label="알림"
        aria-expanded={open}
        aria-controls="qp-notifications"
        onClick={() => setOpen((v) => !v)}
        className="flex h-10 w-10 items-center justify-center rounded-[12px] border border-line bg-bg2 text-ink2 md:h-9 md:w-9 md:rounded-block"
      >
        <IconBell />
      </button>
      {open ? (
        <div
          id="qp-notifications"
          className="absolute right-0 top-11 z-40 w-[300px] rounded-block border border-line bg-bg2 p-3 text-xs shadow-none"
        >
          <div className="mb-2 font-semibold text-ink">리스크 이벤트</div>
          {events.isLoading ? <div className="text-muted">불러오는 중…</div> : null}
          {events.isError ? <div className="text-warn-ink">불러오지 못했습니다</div> : null}
          {!events.isLoading && !items.length && !events.isError ? <div className="text-muted">최근 이벤트가 없습니다</div> : null}
          <ul className="flex flex-col gap-1.5">
            {items.map((e) => (
              <li key={e.id} className="flex justify-between gap-2 rounded-[8px] bg-bg3 px-2.5 py-1.5">
                <span className="text-ink2">
                  {e.kind}
                  {e.resolved_at ? " · 해결됨" : ""}
                </span>
                <span className="num text-muted">{kstRelTime(e.ts)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

const PAGE_TITLES: [string, string][] = [
  ["/judgments", "AI 판단 로그"],
  ["/settings", "설정 · API 키"],
  ["/strategies", "전략 설정"],
  ["/backtests", "백테스트"],
  ["/portfolio", "포트폴리오"],
];

export function Header() {
  const pathname = usePathname() ?? "/";
  const title = PAGE_TITLES.find(([p]) => pathname.startsWith(p))?.[1];
  const health = useHealth();
  const onDashboard = pathname === "/";
  const gates = useGates();
  const conn = useConnection();
  const market = useUi((s) => s.market);
  const setMarket = useUi((s) => s.setMarket);
  const paper = health.data?.paper;
  const showTabs = onDashboard || pathname.startsWith("/trade");
  const g2Waiting = onDashboard && paper && gates.data && !gates.data.g2.pass;

  return (
    <header className="flex h-auto items-center gap-2.5 border-line px-5 pb-3 pt-4 md:h-14 md:gap-6 md:border-b md:bg-bg1 md:px-6 md:py-0">
      <Link href="/" className="flex items-center gap-2.5 text-ink no-underline hover:text-ink md:w-[196px]">
        <span aria-hidden="true" className="flex h-[26px] w-[26px] items-center justify-center rounded-btn bg-ai text-sm font-bold text-bg0">
          Q
        </span>
        <span className="text-base font-bold tracking-[-0.2px]">QuantPilot</span>
      </Link>
      {showTabs ? (
        <div role="group" aria-label="시장" className="hidden gap-1 rounded-block border border-line bg-bg2 p-1 md:flex">
          {MARKET_TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={market === t.value}
              onClick={() => setMarket(t.value)}
              className={cx(
                "h-[30px] rounded-[7px] px-3.5 text-[13px]",
                market === t.value ? "bg-bg3 font-semibold text-ink" : "bg-transparent font-medium text-muted",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      ) : title ? (
        <div className="hidden text-[15px] font-semibold md:block">{title}</div>
      ) : null}
      {paper === undefined ? (
        <span className="skeleton h-7 w-24" aria-hidden="true" />
      ) : (
        <div
          className={cx(
            "flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[11px] font-semibold md:gap-2 md:px-3 md:py-1.5 md:text-xs",
            paper ? "border-warn-line bg-warn-bg text-warn-ink" : "border-up bg-up-bg text-up",
          )}
        >
          <span aria-hidden="true" className={cx("h-1.5 w-1.5 rounded-full md:h-2 md:w-2", paper ? "bg-warn" : "bg-up")} />
          <span className="md:hidden">{paper ? "페이퍼" : "실전"}</span>
          <span className="hidden md:inline">
            {paper ? "페이퍼 모드" : "실전 모드"}
            {g2Waiting ? " · 실전 전환 관문 G2 대기" : ""}
          </span>
        </div>
      )}
      <div className="flex-grow" />
      <Clock />
      <div className="hidden items-center gap-1.5 text-xs text-muted md:flex" role="status" aria-live="polite">
        <span aria-hidden="true" className={cx("h-2 w-2 rounded-full", DOT[conn.tone])} />
        {conn.text}
      </div>
      <Notifications />
      <Link
        href="/settings"
        aria-label="설정 · 계정"
        className="hidden h-9 w-9 items-center justify-center rounded-full border border-line bg-bg3 text-ink2 md:flex"
      >
        <IconUser size={16} />
      </Link>
    </header>
  );
}
