"use client";

// 공통 레이아웃 (docs/10 §3): 헤더 56px + 사이드바 220px + main. 768px 미만은 하단 탭.
// 엔진 다운이면 헤더 연결 상태가 빨강이 되고 상단 배너를 띄운다 (docs/10 §4.1).
import { Header, useConnection } from "@/components/layout/Header";
import { MobileTabBar } from "@/components/layout/MobileTabBar";
import { Sidebar } from "@/components/layout/Sidebar";
import { useLiveChannels, useLiveConnection } from "@/lib/live";
import { useLiveInvalidation } from "@/lib/queries";

const BASE_CHANNELS = ["portfolio", "judgments", "strategy.status", "fills", "orders", "risk"];

function SystemBanner() {
  const conn = useConnection();
  if (!conn.engineDown && !conn.apiDown) return null;
  return (
    <div role="alert" className="border-b border-up bg-up-bg px-6 py-2.5 text-[13px] font-medium text-ink">
      {conn.apiDown
        ? "API 서버에 연결할 수 없습니다. 화면의 값이 최신이 아닐 수 있습니다."
        : "엔진이 응답하지 않습니다 (하트비트 끊김). 자동 매매와 주문 실행이 멈춰 있을 수 있습니다."}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  useLiveConnection();
  useLiveChannels(BASE_CHANNELS);
  useLiveInvalidation();
  return (
    <div className="flex min-h-screen flex-col md:h-screen">
      <a href="#main" className="skip-link">
        본문으로 건너뛰기
      </a>
      <Header />
      <SystemBanner />
      <div className="flex min-h-0 flex-grow">
        <Sidebar />
        <main id="main" tabIndex={-1} className="min-w-0 flex-grow overflow-y-auto pb-[100px] md:pb-0">
          {children}
        </main>
      </div>
      <MobileTabBar />
    </div>
  );
}
