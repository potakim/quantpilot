import { describe, expect, it } from "vitest";
import { DASH } from "@/lib/format";
import {
  cashKrw,
  curvePaths,
  feeRowText,
  scheduleRows,
  shortWhat,
  strategyMddText,
  strategyMonthText,
  todayPnlView,
} from "@/lib/metrics";
import type { Portfolio, ScheduleItem } from "@/lib/types";

function portfolio(today: number | null): Portfolio {
  return {
    total_equity_krw: 10_200_000,
    today_pnl_krw: today,
    total_includes_us: false,
    fx: { usdkrw: null, source: null },
    by_market: {},
    month_pnl: {},
    month_limit: -0.05,
    halted: {},
  };
}

describe("오늘 손익", () => {
  it("값이 있으면 부호 원화·퍼센트·색", () => {
    const v = todayPnlView(portfolio(200_000));
    expect(v.value).toBe("+₩200,000");
    expect(v.pct).toBe("+2.00%");
    expect(v.tone).toBe("up");
    expect(todayPnlView(portfolio(-50_000)).value).toBe("−₩50,000");
  });
  it("null이면 —", () => {
    expect(todayPnlView(portfolio(null))).toEqual({ value: DASH, pct: DASH, tone: "muted" });
    expect(todayPnlView(undefined).value).toBe(DASH);
  });
});

describe("현금 합계 (ADR 0031)", () => {
  const acct = (cash: number, active?: boolean) => ({ active, cash, equity: cash, positions: [], today_pnl: null });
  it("운영 중이 아닌 시장의 초기 현금은 더하지 않는다", () => {
    const p = { ...portfolio(null), by_market: { upbit: acct(9_000_000, true), krx: acct(10_000_000, false), us: acct(10_000, false) } };
    expect(cashKrw(p)).toBe(9_000_000);
  });
  it("저장된 계좌가 있는 시장은 더하고, 미국은 환율이 있을 때만", () => {
    const by = { upbit: acct(9_000_000, true), krx: acct(3_000_000, true), us: acct(1_000, true) };
    expect(cashKrw({ ...portfolio(null), by_market: by })).toBe(12_000_000);
    expect(cashKrw({ ...portfolio(null), by_market: by, fx: { usdkrw: 1_400, source: "x" } })).toBe(13_400_000);
  });
  it("active 필드가 없는 옛 응답은 지금처럼 더한다", () => {
    expect(cashKrw({ ...portfolio(null), by_market: { upbit: acct(1), krx: acct(2) } })).toBe(3);
  });
});

describe("전략 이번 달·MDD", () => {
  it("값이 있으면 퍼센트, MDD는 음수로", () => {
    expect(strategyMonthText(0.05)).toBe("+5.0%");
    expect(strategyMddText(0.05)).toBe("−5.0%");
  });
  it("null이면 —", () => {
    expect(strategyMonthText(null)).toBe(DASH);
    expect(strategyMddText(null)).toBe(DASH);
  });
});

describe("주문 수수료 행", () => {
  it("요율 × 금액, 원화는 정수 원", () => {
    expect(feeRowText(0.0005, 1_000_000, "upbit")).toBe("0.05% ₩500");
    expect(feeRowText(0.0005, 1_234_567, "upbit")).toBe("0.05% ₩617");
    expect(feeRowText(0.001, 250, "us")).toBe("0.1% $0.25");
  });
  it("금액이 없으면 요율만, 요율이 없으면 —", () => {
    expect(feeRowText(0.0005, 0, "upbit")).toBe("0.05%");
    expect(feeRowText(null, 1_000_000, "upbit")).toBe(DASH);
  });
});

describe("오늘 일정", () => {
  const rest: ScheduleItem[] = [
    { name: "krx_close_orders", market: "krx", next_action: { at: "2026-10-14T06:20:00+00:00", what: "GTAA 종가 단일가 주문" }, done: false },
    { name: "upbit_daily_exit", market: "upbit", next_action: { at: "2026-10-14T00:00:00+00:00", what: "청산" }, done: true },
    { name: "vol_breakout", market: "upbit", next_action: { at: "2026-10-14T03:00:00+00:00", what: "목표가 계산 불가" }, done: false },
  ];
  it("REST 항목을 시각순으로, 같은 이름은 WS가 덮는다", () => {
    const now = Date.parse("2026-10-14T03:00:00Z");
    const live = [{ name: "vol_breakout", next_action: { at: "2026-10-14T03:30:00+00:00", what: "KRW-BTC 목표가 ₩52,000,000 돌파 시 진입" } }];
    const rows = scheduleRows(rest, live, now);
    expect(rows.map((r) => r.name)).toEqual(["upbit_daily_exit", "vol_breakout", "krx_close_orders"]);
    expect(rows[0]!.done).toBe(true);
    expect(rows[1]!.what).toBe("KRW-BTC 목표가 ₩52,000,000 돌파 시 진입");
    expect(rows[1]!.market).toBe("upbit"); // WS가 덮어도 REST의 시장은 남는다 (출처 문구용)
    expect(rows.every((r) => r.what !== DASH)).toBe(true);
  });
  it("문구가 없으면 —, 데이터가 없으면 빈 목록", () => {
    const first = rest[0]!;
    const rows = scheduleRows([{ ...first, next_action: { at: first.next_action.at, what: "" } }], []);
    expect(rows[0]!.what).toBe(DASH);
    expect(scheduleRows(undefined, [])).toEqual([]);
  });
});

describe("자산 곡선 좌표", () => {
  it("두 선을 공통 범위로 그린다", () => {
    const pts = [
      { ts: "2026-10-14T00:00:00Z", v: 100 },
      { ts: "2026-10-14T01:00:00Z", v: 200 },
    ];
    const bench = [
      { ts: "2026-10-14T00:00:00Z", v: 100 },
      { ts: "2026-10-14T01:00:00Z", v: 150 },
    ];
    const p = curvePaths(pts, bench, 100, 50)!;
    expect(p.main).toBe("0.0,50.0 100.0,0.0");
    expect(p.bench).toBe("0.0,50.0 100.0,25.0");
    expect([p.min, p.max]).toEqual([100, 200]);
  });
  it("점이 2개 미만이면 null, 벤치마크 없으면 bench null", () => {
    expect(curvePaths([], null, 100, 50)).toBeNull();
    const p = curvePaths(
      [
        { ts: "2026-10-14T00:00:00Z", v: 1 },
        { ts: "2026-10-14T01:00:00Z", v: 1 },
      ],
      null,
      100,
      50,
    )!;
    expect(p.bench).toBeNull();
    expect(p.main).toBe("0.0,25.0 100.0,25.0");
    expect(p.benchEnd).toBeNull();
    expect(p.benchRet).toBeNull();
  });
  it("끝점·기간 수익률·날짜 눈금(KST M/D, 같은 날은 하나)", () => {
    const pts = [
      { ts: "2026-09-04T00:00:00Z", v: 100 },
      { ts: "2026-09-19T00:00:00Z", v: 90 },
      { ts: "2026-10-04T00:00:00Z", v: 110 },
    ];
    const bench = [
      { ts: "2026-09-04T00:00:00Z", v: 100 },
      { ts: "2026-10-04T00:00:00Z", v: 104 },
    ];
    const p = curvePaths(pts, bench, 600, 200)!;
    expect(p.end).toEqual({ x: 600, y: 0 });
    expect(p.ret).toBeCloseTo(0.1);
    expect(p.benchRet).toBeCloseTo(0.04);
    expect(p.benchEnd!.x).toBe(600);
    expect(p.xTicks.map((t) => t.label)).toEqual(["9/4", "9/14", "9/24", "10/4"]);
    expect(p.xTicks.map((t) => t.x)).toEqual([0, 200, 400, 600]);
    const day = curvePaths(
      [
        { ts: "2026-10-04T00:00:00Z", v: 1 },
        { ts: "2026-10-04T03:00:00Z", v: 1 },
      ],
      null,
      600,
      200,
    )!;
    expect(day.xTicks.map((t) => t.label)).toEqual(["10/4"]);
  });
});

describe("일정 문구 줄이기", () => {
  it("여러 건이면 앞 1건 + 외 n건, KRW- 접두어는 뗀다", () => {
    const what = ["KRW-BTC", "KRW-ETH", "KRW-SOL"].map((s) => `${s} 목표가 ₩1 돌파 시 진입`).join(" · ");
    expect(shortWhat(what)).toEqual({
      text: "BTC 목표가 ₩1 돌파 시 진입 외 2건",
      full: "BTC 목표가 ₩1 돌파 시 진입 · ETH 목표가 ₩1 돌파 시 진입 · SOL 목표가 ₩1 돌파 시 진입",
    });
  });
  it("두 건까지는 그대로", () => {
    expect(shortWhat("변동성 돌파 보유분 청산 · 목표가 재계산").text).toBe("변동성 돌파 보유분 청산 · 목표가 재계산");
  });
});
