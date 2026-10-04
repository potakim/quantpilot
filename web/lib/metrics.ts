// 화면 지표 뷰모델 (ADR 0020). API 값 → 화면 문자열. 값이 없으면 "—"(DASH)를 낸다 — 화면은 이 함수만 쓴다.
import { DASH, fmtKrw, fmtPct, fmtSignedKrw, fmtUsd, toneOf, type Tone } from "@/lib/format";
import type { EquityPoint, Portfolio, ScheduleItem } from "@/lib/types";

/** 운영 중인 시장(active)의 현금 합(원). 계좌가 없는 시장의 초기 현금은 빼고 센다 (ADR 0031). */
export function cashKrw(p: Portfolio): number {
  const fx = p.fx.usdkrw ?? null;
  const of = (m: string) => {
    const b = p.by_market[m];
    return b && b.active !== false ? b.cash : 0;
  };
  let cash = of("upbit") + of("krx");
  if (fx) cash += of("us") * fx;
  return cash;
}

/** 대시보드 "오늘 손익" 카드: 값(부호 원화)·퍼센트·색. */
export function todayPnlView(p: Portfolio | undefined | null): { value: string; pct: string; tone: Tone } {
  const amount = p?.today_pnl_krw;
  if (amount == null) return { value: DASH, pct: DASH, tone: "muted" };
  const base = (p?.total_equity_krw ?? 0) - amount;
  const pct = base > 0 ? amount / base : null;
  return { value: fmtSignedKrw(amount), pct: fmtPct(pct, { digits: 2 }), tone: toneOf(amount) };
}

/** 전략 표 "이번 달" 칸. */
export function strategyMonthText(monthPnl: number | null | undefined): string {
  return fmtPct(monthPnl);
}

/** 전략 표 "MDD" 칸: 낙폭은 음수로 보인다 (API는 양수 비율). */
export function strategyMddText(mdd: number | null | undefined): string {
  return fmtPct(mdd == null ? null : -Math.abs(mdd));
}

/** 주문 패널 "수수료" 행: "0.05% ₩500". 원화는 정수 원, 미국은 달러 2자리. 금액이 없으면 요율만. */
export function feeRowText(rate: number | null | undefined, amount: number | null | undefined, market: string): string {
  if (rate == null || !Number.isFinite(rate)) return DASH;
  const pct = `${Number((rate * 100).toFixed(3))}%`;
  if (!amount || !Number.isFinite(amount)) return pct;
  const fee = rate * amount;
  return `${pct} ${market === "us" ? fmtUsd(fee) : fmtKrw(Math.round(fee))}`;
}

export interface ScheduleRow {
  key: string;
  name: string;
  market: string | null;
  at: string;
  what: string;
  done: boolean;
}

/** REST `/schedule` 항목 + WS `strategy.status`의 next_action을 합쳐 시각순으로. 같은 이름은 WS가 이긴다. */
export function scheduleRows(
  rest: ScheduleItem[] | undefined | null,
  live: { name: string; next_action?: { at?: string; what?: string } | null }[],
  now: number = Date.now(),
): ScheduleRow[] {
  const rows = new Map<string, ScheduleRow>();
  for (const it of rest ?? []) {
    if (!it.next_action?.at) continue;
    const key = `${it.name}@${it.next_action.at}`;
    rows.set(key, {
      key,
      name: it.name,
      market: it.market ?? null,
      at: it.next_action.at,
      what: it.next_action.what || DASH,
      done: it.done,
    });
  }
  for (const s of live) {
    const at = s.next_action?.at;
    if (!at) continue;
    let market: string | null = null;
    for (const [k, r] of rows) {
      if (r.name !== s.name) continue;
      market = r.market;
      rows.delete(k);
    }
    const key = `${s.name}@${at}`;
    rows.set(key, { key, name: s.name, market, at, what: s.next_action?.what || DASH, done: Date.parse(at) < now });
  }
  return [...rows.values()].sort((a, b) => a.at.localeCompare(b.at));
}

/** 자산 곡선을 SVG polyline 좌표로 (가로는 시각, 세로는 두 선 공통 범위). 점이 2개 미만이면 null. */
export function curvePaths(
  points: EquityPoint[],
  bench: EquityPoint[] | null | undefined,
  width: number,
  height: number,
): { main: string; bench: string | null; min: number; max: number } | null {
  if (points.length < 2) return null;
  const all = [...points, ...(bench ?? [])];
  const ts = all.map((p) => Date.parse(p.ts));
  const t0 = Math.min(...ts);
  const t1 = Math.max(...ts);
  const vs = all.map((p) => p.v);
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const x = (t: string) => (t1 === t0 ? 0 : ((Date.parse(t) - t0) / (t1 - t0)) * width);
  const y = (v: number) => (max === min ? height / 2 : height - ((v - min) / (max - min)) * height);
  const path = (ps: EquityPoint[]) => ps.map((p) => `${x(p.ts).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  return { main: path(points), bench: bench && bench.length >= 2 ? path(bench) : null, min, max };
}
