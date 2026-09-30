import { describe, expect, it } from "vitest";
import { gaugeTone, lossUsage, monthLossGauge, worstMonthPnl } from "@/lib/gauge";

// 운영자 결정 2026-09-30 (ADR 0018 §4): 사용률 ≤24% ok, ≤60% warn, 그 이상 up
describe("lossUsage", () => {
  it("월 손익 / 한도 비율 (절댓값)", () => {
    expect(lossUsage(-0.012, -0.05)).toBeCloseTo(0.24, 10);
    expect(lossUsage(-0.05, -0.05)).toBe(1);
  });
  it("수익이면 사용률 0", () => {
    expect(lossUsage(0.03, -0.05)).toBe(0);
    expect(lossUsage(0, -0.05)).toBe(0);
  });
  it("한도를 넘어도 비율 그대로 (막대는 100%에서 자른다)", () => {
    expect(lossUsage(-0.07, -0.05)).toBeCloseTo(1.4, 10);
  });
  it("한도가 0이면 0", () => {
    expect(lossUsage(-0.01, 0)).toBe(0);
  });
});

describe("gaugeTone 경계", () => {
  it.each([
    [0, "ok"],
    [0.24, "ok"],
    [0.2401, "warn"],
    [0.6, "warn"],
    [0.6001, "up"],
    [1, "up"],
    [1.5, "up"],
  ] as const)("사용률 %s → %s", (usage, tone) => {
    expect(gaugeTone(usage)).toBe(tone);
  });
});

describe("monthLossGauge", () => {
  it("−1.2% / 한도 −5% (U+2212), 24%는 초록", () => {
    const g = monthLossGauge(-0.012, -0.05);
    expect(g.value).toBe("−1.2%");
    expect(g.limit).toBe("한도 −5%");
    expect(g.text).toBe("−1.2% / 한도 −5%");
    expect(g.tone).toBe("ok");
    expect(g.widthPct).toBeCloseTo(24, 6);
  });
  it("24.01%는 주황", () => {
    expect(monthLossGauge(-0.012005, -0.05).tone).toBe("warn");
  });
  it("60%는 주황, 60.01%는 빨강", () => {
    expect(monthLossGauge(-0.03, -0.05).tone).toBe("warn");
    expect(monthLossGauge(-0.030005, -0.05).tone).toBe("up");
  });
  it("한도 초과는 막대 100%, 빨강", () => {
    const g = monthLossGauge(-0.08, -0.05);
    expect(g.widthPct).toBe(100);
    expect(g.tone).toBe("up");
  });
  it("수익이면 +부호, 사용률 0%, 초록", () => {
    const g = monthLossGauge(0.008, -0.05);
    expect(g.value).toBe("+0.8%");
    expect(g.widthPct).toBe(0);
    expect(g.tone).toBe("ok");
  });
  it("값이 없으면 —", () => {
    const g = monthLossGauge(null, -0.05);
    expect(g.value).toBe("—");
    expect(g.widthPct).toBe(0);
    expect(g.tone).toBe("ok");
  });
});

describe("worstMonthPnl", () => {
  it("시장별 월 손익 중 가장 나쁜 값 (서킷브레이커는 시장별)", () => {
    expect(worstMonthPnl({ upbit: -0.01, krx: 0.02, us: null })).toEqual({
      market: "upbit",
      pnl: -0.01,
    });
  });
  it("모두 null이면 null", () => {
    expect(worstMonthPnl({ upbit: null, krx: null })).toBeNull();
    expect(worstMonthPnl(undefined)).toBeNull();
  });
});
