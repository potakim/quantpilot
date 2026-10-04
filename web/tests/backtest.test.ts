import { describe, expect, it } from "vitest";
import {
  CHART,
  checklist,
  costRows,
  drawdownChart,
  equityChart,
  kpis,
  logTicks,
  normalize,
  periodTable,
  sourcesFor,
  tickLabel,
} from "@/lib/backtest";
import type { BacktestPeriod, BacktestRun, EquityPt, StrategyView } from "@/lib/types";
import { applyMessage, initialLiveState } from "@/lib/ws";

const pts = (vals: number[], start = "2020-01-01"): EquityPt[] =>
  vals.map((v, i) => ({ ts: new Date(Date.parse(start) + i * 86_400_000 * 30).toISOString(), v }));

const period = (key: string, label: string, excess: number | null, start = "2021-01-01"): BacktestPeriod => ({
  key,
  label,
  start,
  end: "2023-12-01",
  cagr: 0.1,
  bench_cagr: excess == null ? null : 0.1 - excess,
  mdd: -0.12,
  bench_mdd: excess == null ? null : -0.3,
  excess,
});

function run(over: Partial<BacktestRun> = {}): BacktestRun {
  return {
    id: 7,
    strategy: "gem",
    source: "yfinance",
    params: {},
    symbols: ["SPY"],
    created_at: "2026-10-04T00:00:00Z",
    period_start: "2008-01-02",
    period_end: "2024-10-01",
    status: "done",
    progress: 1,
    error: null,
    metrics: { cagr: 0.0832, max_drawdown: -0.209, sharpe: 0.7, n_trades: 30, years: 15, total_costs: 1234 },
    cost_model: { fee_rate: 0.0007, slippage_rate: 0.0005, sell_tax_rate: 0 },
    holdout_cutoff: "2024-10-01",
    unlocked_holdout: false,
    attempt_no: 2,
    attempts: { distinct_attempts: 2, warn_after: 7, overfit_warning: false },
    warnings: [],
    equity: pts([100, 150, 400, 1000]),
    drawdown: pts([0, -0.1, -0.05, 0]),
    data_end: "2025-10-01",
    benchmark: { symbol: "SPY", label: "S&P 500 보유", points: pts([100, 120, 200, 300]), drawdown: pts([0, -0.3, 0, 0]) },
    periods: [period("all", "전체 기간", 0.02, "2008-01-02"), period("3y", "최근 3년", -0.048)],
    ...over,
  };
}

describe("실행 설정", () => {
  it("일봉 전략은 시장 실데이터 + 합성, 분봉 미국 전략은 합성만 (ADR 0031)", () => {
    expect(sourcesFor({ market: "us", timeframe: "1M" })).toEqual(["yfinance", "synthetic"]);
    expect(sourcesFor({ market: "upbit", timeframe: "1d" })).toEqual(["upbit", "synthetic"]);
    expect(sourcesFor({ market: "krx", timeframe: "1M" })).toEqual(["fdr", "synthetic"]);
    expect(sourcesFor({ market: "us", timeframe: "5m" })).toEqual(["synthetic"]);
  });

  it("비용 모델 3행: 수수료는 왕복, 미국 양도세는 연 정산이라 따로", () => {
    expect(costRows({ fee_rate: 0.0007, slippage_rate: 0.0005, sell_tax_rate: 0 }, "us")).toEqual([
      ["수수료 (왕복)", "0.14%"],
      ["슬리피지 (편도)", "0.05%"],
      ["매도 세금", "양도세는 연 정산 (별도)"],
    ]);
    expect(costRows({ fee_rate: 0.00015, slippage_rate: 0.001, sell_tax_rate: 0.0018 }, "krx")[2]).toEqual(["매도 세금", "0.18%"]);
    expect(costRows(undefined, "us")).toEqual([]);
  });
});

describe("지표 카드", () => {
  it("벤치마크 보조줄·최근 3년 열위는 주황", () => {
    const k = kpis(run(), "us");
    expect(k.map((x) => x.value)).toEqual(["8.32%", "−20.9%", "0.70", "연 2.0회", "−4.8%p"]);
    expect(k[0]!.sub).toBe("S&P 500 보유 8.00%"); // 전체 기간 벤치마크 CAGR = 0.10 − 0.02
    expect(k[1]!.sub).toBe("S&P 500 보유 −30.0%");
    expect(k[3]!.sub).toBe("총 30회 · 비용 $1,234.00");
    expect(k[4]).toMatchObject({ label: "최근 3년 · 벤치마크 대비", tone: "warn" });
  });

  it("비교 실행이 있으면 카드마다 비교 값", () => {
    const k = kpis(run(), "us", run({ id: 9, metrics: { cagr: 0.05, max_drawdown: -0.1, sharpe: 0.4, n_trades: 10, years: 5 } }));
    expect(k[0]!.compare).toBe("비교 #9 5.00%");
    expect(k[3]!.compare).toBe("비교 #9 연 2.0회");
    expect(k[4]!.compare).toBeUndefined();
  });

  it("벤치마크가 없던 예전 결과는 칸을 비운다 (지어내지 않는다)", () => {
    const k = kpis(run({ benchmark: null, periods: [] }), "upbit");
    expect(k[0]!.sub).toBe("벤치마크 없음");
    expect(k[4]!.value).toBe("—");
  });
});

describe("로그 자산 곡선", () => {
  it("시작 = 100으로 맞춘다", () => {
    expect(normalize([{ ts: "a", v: 5e6 }, { ts: "b", v: 1e7 }]).map((p) => p.v)).toEqual([100, 200]);
    expect(normalize([])).toEqual([]);
  });

  it("10배 넘게 벌어지면 1·2·5 눈금(많으면 10의 거듭제곱만), 아니면 로그 간격", () => {
    expect(logTicks(45, 525)).toEqual([50, 100, 200, 500]);
    expect(logTicks(90, 2600)).toEqual([100, 200, 500, 1000, 2000]);
    expect(logTicks(90, 260_000)).toEqual([100, 1000, 10_000, 100_000]); // 1·2·5면 12개 → 10의 거듭제곱만
    const t = logTicks(95, 160);
    expect(t.length).toBeGreaterThanOrEqual(3);
    expect(t.every((v) => v >= 95 * 0.98 && v <= 160 * 1.02)).toBe(true);
    expect(tickLabel(10_000)).toBe("10k");
    expect(tickLabel(1_500)).toBe("1.5k");
  });

  it("홀드아웃은 컷 ~ 데이터 끝, 열위면 최근 구간 배경, 위로 갈수록 크다", () => {
    const c = equityChart(run())!;
    expect(c.holdout).not.toBeNull();
    expect(c.holdout!.x + c.holdout!.w).toBeCloseTo(CHART.right, 0); // 데이터 끝이 시간 축의 끝
    expect(c.shade).not.toBeNull();
    expect(c.endLabel!.text).toBe("×10");
    const ys = c.main.split(" ").map((p) => Number(p.split(",")[1]));
    expect(ys[0]!).toBeGreaterThan(ys[3]!); // 값이 커지면 y는 작아진다(위)
    expect(c.bench).not.toBeNull();
    expect(equityChart(run({ periods: [period("3y", "최근 3년", 0.01)] }))!.shade).toBeNull();
  });

  it("비교 곡선은 같은 시간 축에 세 번째 선으로", () => {
    const c = equityChart(run(), { equity: pts([100, 110, 130], "2015-01-01") })!;
    expect(c.compare).not.toBeNull();
    expect(Number(c.compare!.split(" ")[0]!.split(",")[0])).toBeCloseTo(CHART.left, 0); // 비교가 더 일찍 시작
  });

  it("낙폭: 0%가 위, 가장 깊은 낙폭이 아래 (벤치마크 포함)", () => {
    const d = drawdownChart(run())!;
    expect(d.floor).toBeCloseTo(-0.3);
    expect(d.main.startsWith(`${CHART.left.toFixed(1)},6`)).toBe(true);
  });
});

describe("구간 표 · 체크리스트", () => {
  it("서버 구간 + 홀드아웃 잠김 행, 초과수익 색", () => {
    const rows = periodTable(run());
    expect(rows.map((r) => r.label)).toEqual([
      "전체 기간 (2008.01 ~ 2023.12)",
      "최근 3년 (2021.01 ~ 2023.12)",
      "2024.10 ~ 2025.10 (홀드아웃)",
    ]);
    expect(rows[0]).toMatchObject({ excess: "+2.0%p", excessTone: "up" });
    expect(rows[1]).toMatchObject({ excess: "−4.8%p", excessTone: "down" });
    expect(rows[2]).toMatchObject({ cagr: "잠김", locked: true });
    expect(periodTable(run({ unlocked_holdout: true })).some((r) => r.locked)).toBe(false);
  });

  it("체크리스트: 비용 포함 · G1 · 시도 횟수 · 열위 · 홀드아웃 안내", () => {
    const s = { gate: { g1: { pass: true } } } as unknown as StrategyView;
    const items = checklist(run(), s);
    expect(items.map((i) => i.mark)).toEqual(["ok", "ok", "ok", "warn", "todo"]);
    const many = checklist(run({ attempts: { distinct_attempts: 8, warn_after: 7, overfit_warning: true } }), undefined);
    expect(many[1]).toMatchObject({ mark: "warn" });
    expect(many[2]!.text).toContain("과최적화");
  });
});

describe("WS 진행률 backtest:{id}", () => {
  it("진행률·완료를 실행 id별로 담는다", () => {
    const s = initialLiveState();
    const next = applyMessage(s, { ch: "backtest:7", ts: null, data: { progress: 0.1, stage: "running" } });
    expect(next.backtests).toEqual({ "7": { progress: 0.1, stage: "running", done: false, status: null, error: null } });
    const done = applyMessage({ ...s, ...next }, { ch: "backtest:7", ts: null, data: { progress: 1, stage: "done", done: true, status: "done" } });
    expect(done.backtests!["7"]).toMatchObject({ done: true, status: "done" });
    expect(applyMessage(s, { ch: "backtest:7", ts: null, data: { stage: "x" } })).toEqual({});
  });
});
