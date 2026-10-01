// 월 손실 한도 게이지 (docs/10 §3, ADR 0018 §4 — 운영자 결정 2026-09-30).
// 사용률 = |월 손익| ÷ |월 한도|. ≤24% 초록(ok) · ≤60% 주황(warn) · 그 이상 빨강(up). 수익이면 0%.
import { DASH, fmtPct } from "@/lib/format";

export type GaugeTone = "ok" | "warn" | "up";

export const OK_MAX = 0.24;
export const WARN_MAX = 0.6;

/** 한도 대비 손실 사용률 (0 이상, 한도를 넘으면 1보다 크다). */
export function lossUsage(monthPnl: number, monthLimit: number): number {
  if (!Number.isFinite(monthPnl) || !Number.isFinite(monthLimit) || monthLimit === 0) return 0;
  if (monthPnl >= 0) return 0;
  return Math.abs(monthPnl) / Math.abs(monthLimit);
}

/** 사용률 → 색. 경계값은 아래 단계에 포함한다 (24%는 초록, 60%는 주황). */
export function gaugeTone(usage: number): GaugeTone {
  if (usage <= OK_MAX + 1e-12) return "ok";
  if (usage <= WARN_MAX + 1e-12) return "warn";
  return "up";
}

export interface MonthLossGauge {
  value: string;
  limit: string;
  text: string;
  tone: GaugeTone;
  usage: number;
  widthPct: number;
}

/** 카드에 그릴 값 한 벌: `−1.2% / 한도 −5%`, 색, 막대 폭(0~100). */
export function monthLossGauge(monthPnl: number | null | undefined, monthLimit: number): MonthLossGauge {
  const limit = `한도 ${fmtPct(monthLimit, { signed: false, digits: 0 })}`;
  if (monthPnl == null || !Number.isFinite(monthPnl)) {
    return { value: DASH, limit, text: `${DASH} / ${limit}`, tone: "ok", usage: 0, widthPct: 0 };
  }
  const usage = lossUsage(monthPnl, monthLimit);
  const value = fmtPct(monthPnl);
  return {
    value,
    limit,
    text: `${value} / ${limit}`,
    tone: gaugeTone(usage),
    usage,
    widthPct: Math.min(100, usage * 100),
  };
}

/** 시장별 월 손익 중 가장 나쁜 값. 서킷브레이커는 시장마다 따로 걸리므로 가장 가까운 시장을 보여 준다. */
export function worstMonthPnl(
  byMarket: Record<string, number | null | undefined> | null | undefined,
): { market: string; pnl: number } | null {
  let worst: { market: string; pnl: number } | null = null;
  for (const [market, pnl] of Object.entries(byMarket ?? {})) {
    if (pnl == null || !Number.isFinite(pnl)) continue;
    if (worst === null || pnl < worst.pnl) worst = { market, pnl };
  }
  return worst;
}
