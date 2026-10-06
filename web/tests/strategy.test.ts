import { describe, expect, it } from "vitest";
import {
  allocationMax,
  allocationSegments,
  allocationSummary,
  formatParam,
  g1Summary,
  judgeSavedNote,
  llmModels,
  modeBadge,
  orderStrategies,
  paramControls,
  paramsDiffer,
  restartPending,
  snap,
} from "@/lib/strategy";
import type { JudgeView, ParamSpecView, StrategyView } from "@/lib/types";

function strat(name: string, horizon: string, allocation: number, extra: Partial<StrategyView> = {}): StrategyView {
  return {
    name,
    market: "upbit",
    timeframe: "1d",
    horizon,
    symbols: [],
    params: {},
    enabled: true,
    allocation,
    paper: true,
    status: { position: {}, month_pnl: null, mdd_30d: null },
    gate: {},
    ...extra,
  };
}

// 권장 조합 (ADR 0032)
const DEFAULTS = [
  strat("vol_breakout", "intraday", 0.15),
  strat("gem", "long", 0.4),
  strat("gtaa", "long", 0.35),
  strat("orb", "intraday", 0),
];

const spec = (s: Partial<ParamSpecView> & { name: string }): ParamSpecView => ({
  default: null,
  min: null,
  max: null,
  step: null,
  choices: [],
  description: "",
  ...s,
});

describe("자본 배분", () => {
  it("화면 원본 순서: 장기 → 단타 → 현금 → 배분 0은 점선", () => {
    const seg = allocationSegments(DEFAULTS);
    expect(seg.map((g) => [g.key, Math.round(g.share * 100), g.dashed])).toEqual([
      ["gem", 40, false],
      ["gtaa", 35, false],
      ["vol_breakout", 15, false],
      ["cash", 10, false],
      ["orb", 0, true],
    ]);
    expect(seg.find((g) => g.key === "vol_breakout")!.color).toBe("bg-ai");
  });

  it("보유 기간별 합계 문구", () => {
    expect(allocationSummary(DEFAULTS)).toBe("장기 75% · 단타 15% · 현금 10%");
  });

  it("최대 배분 = min(전체 합 1, 단타 상한) — API PATCH 검사와 같다", () => {
    expect(allocationMax(DEFAULTS, "vol_breakout", 0.2)).toBe(0.2); // 단타 상한이 먼저 (남은 합 0.25)
    expect(allocationMax(DEFAULTS, "orb", 0.2)).toBe(0.05); // 단타 0.15가 이미 있다
    expect(allocationMax(DEFAULTS, "gem", 0.2)).toBe(0.5); // 1 − 0.15 − 0.35
    expect(allocationMax(DEFAULTS, "nope", 0.2)).toBe(0);
  });

  it("카드 순서는 원본 2×2 (GEM · 변동성 돌파 / GTAA · ORB)", () => {
    expect(orderStrategies(DEFAULTS).map((s) => s.name)).toEqual(["gem", "vol_breakout", "gtaa", "orb"]);
  });
});

describe("파라미터", () => {
  const schema = [
    spec({ name: "k", default: 0.5, min: 0.3, max: 0.8, step: 0.05 }),
    spec({ name: "target_vol", default: 0.01, min: 0.002, max: 0.03, step: 0.001 }),
    spec({ name: "ma_windows", default: [3, 5, 10, 20] }),
    spec({ name: "noise_k", default: false, choices: [true, false] }),
    spec({ name: "equity_us", default: "SPY" }), // 설명 없는 글자 = 표시 안 함 (종목 칩이 대신)
    spec({ name: "exit_time", default: "15:55", description: "강제 청산 시각 (ET)" }),
  ];

  it("숫자 범위 = 슬라이더, 참·거짓 = 칩 토글, 목록 = 칩, 설명 있는 글자 = 한 줄", () => {
    const c = paramControls(schema, { k: 0.6 });
    expect(c.map((x) => [x.kind, x.spec.name])).toEqual([
      ["slider", "k"],
      ["slider", "target_vol"],
      ["chips", "ma_windows"],
      ["flag", "noise_k"],
      ["text", "exit_time"],
    ]);
    expect(c[0]).toMatchObject({ value: 0.6, label: "K 값" });
    expect(c[2]).toMatchObject({ chips: ["3", "5", "10", "20"] });
  });

  it("값 표시: 비율은 %, 목표 배수 R, 이평 기간 일", () => {
    expect(formatParam(schema[0]!, 0.5)).toBe("0.50");
    expect(formatParam(schema[1]!, 0.01)).toBe("1.0%");
    expect(formatParam(spec({ name: "max_weight", step: 0.1 }), 1)).toBe("100%");
    expect(formatParam(spec({ name: "target_r", step: 1 }), 10)).toBe("10R");
    expect(formatParam(spec({ name: "ma_days", step: 10 }), 210)).toBe("210일");
  });

  it("기본값과 다르면 표시 (G1은 기본값 기준)", () => {
    expect(paramsDiffer(schema, {})).toBe(false);
    expect(paramsDiffer(schema, { k: 0.5, ma_windows: [3, 5, 10, 20] })).toBe(false);
    expect(paramsDiffer(schema, { k: 0.6 })).toBe(true);
  });

  it("슬라이더 값은 step 격자로 (부동소수 오차 없음)", () => {
    expect(snap(0.30000000000000004 + 0.05 * 3, schema[0]!)).toBe(0.45);
    expect(snap(0.0123, schema[1]!)).toBe(0.012);
  });
});

describe("G1 요약", () => {
  it("기간·CAGR·MDD (GEM은 월말 MDD)", () => {
    const vb = { period: "2017-09-25~2025-10-03", metrics: { cagr: 0.0633, mdd: -0.0853, entries: 3719 } };
    expect(g1Summary(vb)).toEqual({ period: "2017-09-25~2025-10-03", text: "CAGR +6.3% · MDD −8.5%" });
    const gem = { period: "2008-01-02~2025-10-02", metrics: { cagr: 0.083, mdd_monthly: -0.209 } };
    expect(g1Summary(gem)?.text).toBe("CAGR +8.3% · MDD −20.9%");
  });

  it("근거가 없으면 null — 숫자를 지어내지 않는다", () => {
    expect(g1Summary(undefined)).toBeNull();
    expect(g1Summary({ period: "x" })).toBeNull();
  });
});

describe("AI 판단 설정", () => {
  const judge = (over: Partial<JudgeView> = {}): JudgeView => ({
    provider: "stub",
    llm_models: ["stub", "stub"],
    hold_below: 0.5,
    full_above: 0.9,
    keys: { typesafe: false, claude: false, gemini: false },
    active: { provider: "stub", llm_models: ["stub", "stub"] },
    news_summary: true,
    ...over,
  });

  it("저장 안내는 키마다 적용 시점을 알린다 (ADR 0032·0035)", () => {
    expect(judgeSavedNote(["news.enabled"])).toBe("뉴스 요약은 다음 정시 수집부터 적용됩니다.");
    expect(judgeSavedNote(["gate.hold_below"])).toBe("임계값은 엔진이 5초 안에 반영합니다.");
    expect(judgeSavedNote(["llm.models", "news.enabled"])).toBe(
      "판단 모델·리뷰어는 엔진을 다시 켜면 적용됩니다. 뉴스 요약은 다음 정시 수집부터 적용됩니다.",
    );
  });

  it("리뷰어는 늘 2개 (불변식 #8), 끈 자리는 stub", () => {
    expect(llmModels(true, true)).toEqual(["claude", "gemini"]);
    expect(llmModels(false, true)).toEqual(["stub", "gemini"]);
  });

  it("저장값 ≠ 엔진이 쓰는 값이면 재시작 대기, 엔진 기록이 없으면 모름", () => {
    expect(restartPending(judge())).toBe(false);
    expect(restartPending(judge({ provider: "typesafe" }))).toBe(true);
    expect(restartPending(judge({ llm_models: ["claude", "stub"] }))).toBe(true);
    expect(restartPending(judge({ active: null, provider: "typesafe" }))).toBe(false);
  });
});

describe("modeBadge", () => {
  it("페이퍼 전용 전략은 모드와 무관하게 '페이퍼 전용'", () => {
    for (const app of [true, false, undefined]) {
      expect(modeBadge({ name: "orb", paper: false }, app)).toEqual({ label: "페이퍼 전용", tone: "warn" });
    }
  });

  it("실전 모드에서는 전략별로 '페이퍼만' / '실전' (대시보드와 같은 말)", () => {
    expect(modeBadge({ name: "gem", paper: true }, false)).toEqual({ label: "페이퍼만", tone: "warn" });
    expect(modeBadge({ name: "vol_breakout", paper: false }, false)).toEqual({ label: "실전", tone: "up" });
  });

  it("페이퍼 모드의 보통 전략은 배지 없음, 실전으로 바꿔 둔 전략만 따로 알린다", () => {
    expect(modeBadge({ name: "gem", paper: true }, true)).toBeNull();
    const b = modeBadge({ name: "vol_breakout", paper: false }, true);
    expect(b?.label).toBe("실전 전환됨");
    expect(b?.hint).toContain("페이퍼로 실행");
  });

  it("전체 모드를 아직 모르면 배지 없음", () => {
    expect(modeBadge({ name: "gem", paper: true }, undefined)).toBeNull();
  });
});
