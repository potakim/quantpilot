import { describe, expect, it } from "vitest";
import { holdingRows, marketCards, strategyUsage } from "@/lib/portfolio";
import type { Portfolio, PositionView, StrategyView } from "@/lib/types";

const LIVE = new Set(["upbit"]);

function portfolio(over: Partial<Portfolio> = {}): Portfolio {
  return {
    total_equity_krw: 12_000_000,
    today_pnl_krw: 50_000,
    total_includes_us: true,
    fx: { usdkrw: null, source: null },
    by_market: {
      upbit: {
        active: true,
        cash: 9_000_000,
        equity: 10_000_000,
        positions: [{ market: "upbit", symbol: "KRW-BTC", strategy: "vol_breakout", qty: 0.01, avg_price: 1e8, stop: null, opened_at: null, price: null, unrealized: null }],
        today_pnl: { amount: 50_000, pct: 0.005 },
      },
      krx: { active: true, cash: 1_500_000, equity: 2_000_000, positions: [], today_pnl: null },
      us: { active: false, cash: 10_000, equity: 10_000, positions: [], today_pnl: null },
    },
    month_pnl: { upbit: -0.02, krx: 0.01, us: null },
    month_limit: -0.05,
    halted: { upbit: null, krx: null, us: null },
    ...over,
  };
}

const pos = (over: Partial<PositionView>): PositionView => ({
  market: "upbit",
  symbol: "KRW-BTC",
  strategy: "vol_breakout",
  qty: 0.01,
  avg_price: 100_000_000,
  stop: 95_000_000,
  opened_at: "2026-10-04T00:00:00Z",
  price: 110_000_000,
  unrealized: 100_000,
  ...over,
});

describe("시장별 계좌 카드", () => {
  it("운영 중 · 2단계 예정 · 계좌 없음을 나눈다 (ADR 0031)", () => {
    const [up, krx, us] = marketCards(portfolio(), LIVE);
    expect(up).toMatchObject({ name: "업비트", status: { text: "운영 중" }, equity: "₩10,000,000", invested: "₩1,000,000", positions: 1 });
    expect(up!.cashShare).toBeCloseTo(0.9);
    expect(up!.today.text).toBe("+₩50,000 (+0.50%)");
    expect(up!.month.value).toBe("−2.0%");
    expect(krx).toMatchObject({ status: { text: "2단계 예정" }, active: true, equity: "₩2,000,000" });
    expect(us).toMatchObject({ active: false, equity: "—" });
  });

  it("할트면 상태가 할트", () => {
    const [up] = marketCards(portfolio({ halted: { upbit: "reconcile_mismatch", krx: null, us: null } }), LIVE);
    expect(up).toMatchObject({ status: { text: "할트", tone: "warn" }, halted: "reconcile_mismatch" });
  });
});

describe("보유 종목 표", () => {
  it("평가액·평가손익·계좌 비중·손절까지 거리, 평가액 큰 순", () => {
    const rows = holdingRows(
      [pos({}), pos({ symbol: "KRW-ETH", qty: 1, avg_price: 4_000_000, price: 4_400_000, unrealized: 400_000, stop: null })],
      portfolio(),
    );
    expect(rows.map((r) => r.name)).toEqual(["ETH", "BTC"]); // 440만 > 110만
    expect(rows[1]).toMatchObject({
      value: "₩1,100,000",
      pnl: "+₩100,000",
      pnlPct: "+10.00%",
      tone: "up",
      weight: "11.0%",
      stopGap: "−13.6%",
      strategy: "변동성 돌파",
    });
    expect(rows[0]!.stopGap).toBe("없음");
  });

  it("현재가가 없으면 평균단가로 평가하고 손익은 비운다", () => {
    const [r] = holdingRows([pos({ price: null, unrealized: null })], portfolio());
    expect(r).toMatchObject({ priced: false, price: "—", pnl: "—", value: "₩1,000,000" });
  });

  it("시장 탭으로 거르고, 수량 0은 뺀다", () => {
    const list = [pos({}), pos({ market: "krx", symbol: "360750", qty: 10, avg_price: 15_000, price: 15_500, stop: null }), pos({ symbol: "KRW-SOL", qty: 0 })];
    expect(holdingRows(list, portfolio(), "krx").map((r) => r.symbol)).toEqual(["360750"]);
    expect(holdingRows(list, portfolio()).length).toBe(2);
  });
});

describe("전략별 배정 대비 투입 (ADR 0032)", () => {
  const strat = (name: string, market: string, allocation: number, enabled = true) =>
    ({ name, market, allocation, enabled }) as unknown as StrategyView;

  it("배정 = 시장 계좌 × 배분, 투입 = 그 전략 보유 평가액", () => {
    const u = strategyUsage([strat("vol_breakout", "upbit", 0.15), strat("gem", "us", 0.4), strat("orb", "us", 0)], [pos({})], portfolio(), LIVE);
    expect(u.map((x) => x.name)).toEqual(["vol_breakout", "gem"]); // 배분 0은 뺀다
    expect(u[0]).toMatchObject({ target: "₩1,500,000", used: "₩1,100,000", live: true });
    expect(u[0]!.ratio).toBeCloseTo(1_100_000 / 1_500_000);
    expect(u[1]).toMatchObject({ target: "2단계 예정", live: false, ratio: null });
  });

  it("배정보다 5% 넘게 더 들고 있으면 주황 경고", () => {
    const big = pos({ qty: 0.02 }); // 220만 / 배정 150만
    const [u] = strategyUsage([strat("vol_breakout", "upbit", 0.15)], [big], portfolio(), LIVE);
    expect(u).toMatchObject({ over: true });
    expect(u!.note).toContain("배정보다 많이 보유 중 (147%)");
    const [ok] = strategyUsage([strat("vol_breakout", "upbit", 0.15)], [pos({})], portfolio(), LIVE);
    expect(ok!.over).toBe(false);
  });

  it("꺼진 전략과 보유 없는 전략은 설명이 다르다", () => {
    const off = strategyUsage([strat("vol_breakout", "upbit", 0.15, false)], [], portfolio(), LIVE);
    expect(off[0]!.note).toContain("꺼짐");
    const idle = strategyUsage([strat("vol_breakout", "upbit", 0.15)], [], portfolio(), LIVE);
    expect(idle[0]!.note).toContain("보유 없음");
  });
});
