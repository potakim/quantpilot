// 포트폴리오 화면 계산: 시장별 계좌 카드 · 보유 종목 표 · 전략별 배정 대비 투입.
// 숫자는 /portfolio(시장 계좌)·/positions(현재가·미실현)·/strategies(배분)에서 온다. 테스트: tests/portfolio.test.ts.
import { monthLossGauge, type MonthLossGauge } from "@/lib/gauge";
import { DASH, fmtKrw, fmtPct, fmtPrice, fmtQty, fmtUsd, shortSymbol, toneOf, type Tone } from "@/lib/format";
import { MARKET_LABEL, strategyLabel } from "@/lib/labels";
import type { Portfolio, PositionView, StrategyView } from "@/lib/types";

export const MARKETS = ["upbit", "krx", "us"] as const;

/** 시장 통화로 금액 표시 (미국 달러, 나머지 원화). */
export const money = (v: number | null | undefined, market: string) =>
  v == null ? DASH : market === "us" ? fmtUsd(v) : fmtKrw(v);

const signedMoney = (v: number | null | undefined, market: string) =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${money(v, market)}`;

export interface MarketCard {
  market: string;
  name: string;
  status: { text: string; tone: "ok" | "warn" | "muted" };
  active: boolean;
  equity: string;
  cash: string;
  invested: string;
  cashShare: number | null; // 0~1
  today: { text: string; tone: Tone };
  month: MonthLossGauge;
  halted: string | null;
  positions: number;
}

/** 시장별 계좌 카드. 실시간 엔진이 없는 시장은 "2단계 예정", 할트면 "할트" (ADR 0031). */
export function marketCards(p: Portfolio | undefined, live: Set<string>): MarketCard[] {
  return MARKETS.map((m) => {
    const b = p?.by_market[m];
    const active = b ? b.active !== false : false;
    const halted = p?.halted[m] ?? null;
    const status = halted
      ? { text: "할트", tone: "warn" as const }
      : live.has(m)
        ? { text: "운영 중", tone: "ok" as const }
        : { text: "2단계 예정", tone: "muted" as const };
    const equity = active && b ? b.equity : null;
    const cash = active && b ? b.cash : null;
    const t = active ? b?.today_pnl : null;
    return {
      market: m,
      name: MARKET_LABEL[m] ?? m,
      status,
      active,
      equity: money(equity, m),
      cash: money(cash, m),
      invested: equity != null && cash != null ? money(equity - cash, m) : DASH,
      cashShare: equity && cash != null ? cash / equity : null,
      today: t
        ? { text: `${signedMoney(t.amount, m)} (${fmtPct(t.pct, { digits: 2 })})`, tone: toneOf(t.amount) }
        : { text: DASH, tone: "muted" },
      month: monthLossGauge(active ? p?.month_pnl[m] : null, p?.month_limit ?? Number.NaN),
      halted,
      positions: active && b ? b.positions.filter((x) => x.qty > 0).length : 0,
    };
  });
}

export interface HoldingRow {
  key: string;
  market: string;
  symbol: string;
  name: string;
  strategy: string;
  qty: string;
  avg: string;
  price: string;
  value: string;
  pnl: string;
  pnlPct: string;
  tone: Tone;
  weight: string;
  stopGap: string; // 현재가에서 손절가까지 (음수 = 아래로)
  priced: boolean; // 현재가를 받았는가 (없으면 평균단가로 평가)
  valueNum: number;
}

/** 보유 종목 표. 계좌 비중 = 평가액 ÷ 그 시장 계좌 평가액, 손절까지 = 손절가 ÷ 현재가 − 1. 평가액 큰 순. */
export function holdingRows(positions: PositionView[], p: Portfolio | undefined, marketFilter = "all"): HoldingRow[] {
  return positions
    .filter((x) => x.qty > 0 && (marketFilter === "all" || x.market === marketFilter))
    .map((x) => {
      const m = x.market ?? "upbit";
      const priced = x.price != null;
      const px = x.price ?? x.avg_price;
      const value = x.qty * px;
      const acct = p?.by_market[m]?.equity;
      const pct = x.avg_price > 0 && priced ? px / x.avg_price - 1 : null;
      return {
        key: `${m}:${x.symbol}:${x.strategy ?? ""}`,
        market: m,
        symbol: x.symbol,
        name: m === "upbit" ? shortSymbol(x.symbol) : x.symbol,
        strategy: x.strategy ? strategyLabel(x.strategy) : "수동",
        qty: fmtQty(x.qty, m),
        avg: fmtPrice(x.avg_price, m),
        price: priced ? fmtPrice(px, m) : DASH,
        value: money(value, m),
        pnl: priced ? signedMoney(x.unrealized ?? value - x.qty * x.avg_price, m) : DASH,
        pnlPct: pct == null ? DASH : fmtPct(pct, { digits: 2 }),
        tone: pct == null ? "muted" : toneOf(pct),
        weight: acct ? fmtPct(value / acct, { signed: false, digits: 1 }) : DASH,
        stopGap: x.stop != null && px > 0 ? fmtPct(x.stop / px - 1, { digits: 1 }) : "없음",
        priced,
        valueNum: value,
      };
    })
    .sort((a, b) => b.valueNum - a.valueNum);
}

export interface StrategyUsage {
  name: string;
  label: string;
  market: string;
  live: boolean;
  allocation: string;
  target: string;
  used: string;
  ratio: number | null; // 투입 ÷ 배정
  over: boolean; // 배정보다 5% 넘게 더 들고 있다 (배분을 줄였거나 수동 주문)
  note: string;
}

/**
 * 전략별 배정 대비 투입: 배정 자본 = 그 시장 계좌 평가액 × 배분 (ADR 0032 §4·§5), 투입 = 그 전략 보유 평가액.
 * 배분 0 전략은 뺀다. 엔진이 없는 시장은 "2단계 예정".
 */
export function strategyUsage(
  strategies: StrategyView[],
  positions: PositionView[],
  p: Portfolio | undefined,
  live: Set<string>,
): StrategyUsage[] {
  return strategies
    .filter((s) => s.allocation > 0)
    .map((s) => {
      const isLive = live.has(s.market);
      const acct = p?.by_market[s.market];
      const target = isLive && acct ? acct.equity * s.allocation : null;
      const used = positions
        .filter((x) => x.strategy === s.name && x.qty > 0 && (x.market ?? s.market) === s.market)
        .reduce((a, x) => a + x.qty * (x.price ?? x.avg_price), 0);
      const ratio = target ? used / target : null;
      const over = ratio != null && ratio > 1.05;
      return {
        name: s.name,
        label: strategyLabel(s.name),
        market: s.market,
        live: isLive,
        allocation: fmtPct(s.allocation, { signed: false, digits: 0 }),
        target: isLive ? money(target, s.market) : "2단계 예정",
        used: money(used, s.market),
        ratio,
        over,
        note: !isLive
          ? "엔진 연결 전 — 배분만 잡혀 있습니다"
          : !s.enabled
            ? "꺼짐 — 새 진입 없음"
            : used === 0
              ? "보유 없음 — 신호를 기다리는 중"
              : over
                ? `배정보다 많이 보유 중 (${fmtPct(ratio, { signed: false, digits: 0 })}) — 배분을 줄였거나 수동 주문`
                : `배정의 ${fmtPct(ratio, { signed: false, digits: 0 })} 사용 중`,
      };
    });
}
