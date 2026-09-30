import { describe, expect, it } from "vitest";
import { applyTick, movingAverage, TF_SECONDS, toBars } from "@/lib/chart-data";
import { answerRows, gateLabel, judgmentResult, verdictLabel } from "@/lib/labels";

describe("캔들 변환·이동평균", () => {
  const raw = [
    { ts: "2026-09-30T00:00:00+00:00", o: 1, h: 3, l: 0.5, c: 2, v: 10 },
    { ts: "2026-09-30T00:05:00+00:00", o: 2, h: 4, l: 1.5, c: 3, v: 5 },
  ];
  it("API 캔들 → 초 단위 time", () => {
    const bars = toBars(raw);
    expect(bars[0]).toEqual({ time: 1790726400, open: 1, high: 3, low: 0.5, close: 2, volume: 10 });
  });
  it("n개 이동평균 (앞쪽 n−1개는 없음)", () => {
    const bars = toBars([...raw, { ...raw[1]!, ts: "2026-09-30T00:10:00+00:00", c: 4 }]);
    expect(movingAverage(bars, 2)).toEqual([
      { time: bars[1]!.time, value: 2.5 },
      { time: bars[2]!.time, value: 3.5 },
    ]);
    expect(movingAverage(bars, 5)).toEqual([]);
  });
});

describe("applyTick", () => {
  const t0 = 1790726400; // 5분 경계
  const bars = [{ time: t0, open: 10, high: 12, low: 9, close: 11, volume: 1 }];
  it("같은 봉 안이면 종가·고가·저가 갱신", () => {
    const b = applyTick(bars, TF_SECONDS["5m"]!, t0 + 60, 13);
    expect(b).toEqual({ time: t0, open: 10, high: 13, low: 9, close: 13, volume: 1 });
  });
  it("다음 봉이면 새 봉", () => {
    const b = applyTick(bars, TF_SECONDS["5m"]!, t0 + 300 + 5, 8);
    expect(b).toEqual({ time: t0 + 300, open: 8, high: 8, low: 8, close: 8, volume: 0 });
  });
  it("과거 틱·봉 없음은 null", () => {
    expect(applyTick(bars, 300, t0 - 10, 8)).toBeNull();
    expect(applyTick([], 300, t0, 8)).toBeNull();
  });
});

describe("판단 라벨", () => {
  it("게이트 → 결과 문구 (색만으로 전달하지 않는다)", () => {
    expect(gateLabel("full")).toEqual({ text: "진입 허용 · 전체 사이징", tone: "ok" });
    expect(gateLabel("half")).toEqual({ text: "진입 허용 · 절반 사이징", tone: "ok" });
    expect(gateLabel("hold")).toEqual({ text: "보류", tone: "warn" });
    expect(judgmentResult("full")).toEqual({ text: "진입 100%", tone: "ok" });
    expect(judgmentResult("half")).toEqual({ text: "진입 50%", tone: "ok" });
    expect(judgmentResult("hold")).toEqual({ text: "보류", tone: "warn" });
    expect(judgmentResult("weird")).toEqual({ text: "weird", tone: "muted" });
  });
  it("LLM 판정", () => {
    expect(verdictLabel({ model: "claude-sonnet-5", approve: true })).toEqual({
      name: "Claude",
      short: "C",
      text: "승인",
      approve: true,
    });
    expect(verdictLabel({ model: "gemini-3.5-flash", approve: false }).text).toBe("보류");
    expect(verdictLabel({ model: "stub", approve: true }).name).toBe("stub");
  });
  it("원자 질문: regime은 최고 확률 라벨, signal_quality는 5점 만점", () => {
    const rows = answerRows({
      regime: { trend_up: 0.79, range: 0.16, trend_down: 0.05 },
      news_risk: 0.12,
      signal_quality: 4.2,
    });
    expect(rows).toEqual([
      { key: "regime", text: "trend_up 0.79", width: 0.79, tone: "ai", full: "trend_up 0.79 · range 0.16 · trend_down 0.05" },
      { key: "news_risk", text: "0.12", width: 0.12, tone: "ok", full: "0.12" },
      { key: "signal_quality", text: "4.2 / 5", width: 0.84, tone: "ai", full: "4.2 / 5" },
    ]);
  });
  it("리스크 질문이 0.5 이상이면 주황", () => {
    expect(answerRows({ news_risk: 0.63 })[0]!.tone).toBe("warn");
  });
});
