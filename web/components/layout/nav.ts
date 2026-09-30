import { IconAi, IconBacktest, IconDashboard, IconPortfolio, IconSettings, IconStrategy, IconTrade } from "@/components/ui/Icons";

// 내비 항목 (Main.dc.html 사이드바, Mobile.dc.html 하단 탭). 전략·백테스트·포트폴리오는 2단계 화면.
export const SIDE_NAV = [
  { href: "/", label: "대시보드", Icon: IconDashboard },
  { href: "/trade", label: "거래 · 차트", Icon: IconTrade },
  { href: "/strategies", label: "전략 설정", Icon: IconStrategy },
  { href: "/judgments", label: "AI 판단 로그", Icon: IconAi },
  { href: "/backtests", label: "백테스트", Icon: IconBacktest },
  { href: "/portfolio", label: "포트폴리오", Icon: IconPortfolio },
] as const;

export const SETTINGS_NAV = { href: "/settings", label: "설정 · API 키", Icon: IconSettings } as const;

export const MOBILE_NAV = [
  { href: "/", label: "홈", Icon: IconDashboard },
  { href: "/trade", label: "거래", Icon: IconTrade },
  { href: "/strategies", label: "전략", Icon: IconStrategy },
  { href: "/judgments", label: "AI 판단", Icon: IconAi },
  { href: "/settings", label: "설정", Icon: IconSettings },
] as const;

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
