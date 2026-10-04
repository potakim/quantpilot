// 백테스트 화면(Backtest.dc.html)의 계산: 데이터 소스·비용 표시·지표 카드·로그 자산 곡선·낙폭·구간 표·체크리스트.
// 숫자 정의는 ADR 0033. 화면 컴포넌트는 그리기만 한다 (테스트: tests/backtest.test.ts).
import { DASH, fmtKrw, fmtNumber, fmtPct, fmtUsd } from "@/lib/format";
import type { BacktestPeriod, BacktestRun, CostModelView, EquityPt, StrategyView } from "@/lib/types";

// ---------- 실행 설정 ----------

export const SOURCE_LABEL: Record<string, string> = {
  synthetic: "합성 데이터 (연습용)",
  upbit: "업비트 실데이터",
  yfinance: "야후 파이낸스 (미국)",
  fdr: "FinanceDataReader (국내)",
};

const REAL_SOURCE: Record<string, string> = { upbit: "upbit", us: "yfinance", krx: "fdr" };

/** 고를 수 있는 데이터 소스: 시장의 실데이터 + 합성. 야후·FDR은 일봉뿐이라 분봉 전략은 합성만 (ADR 0031). */
export function sourcesFor(s: Pick<StrategyView, "market" | "timeframe">): string[] {
  const real = REAL_SOURCE[s.market];
  const daily = s.timeframe === "1d" || s.timeframe === "1M";
  return real && (daily || real === "upbit") ? [real, "synthetic"] : ["synthetic"];
}

/** 비용 모델 3행 (편도 비율 → 화면 문구). 백테스터·PaperBroker와 같은 값 (불변식 #4). */
export function costRows(c: CostModelView | undefined, market: string): [string, string][] {
  if (!c) return [];
  const pct = (v: number) => fmtPct(v, { signed: false, digits: 2 });
  return [
    ["수수료 (왕복)", pct(2 * c.fee_rate)],
    ["슬리피지 (편도)", pct(c.slippage_rate)],
    [
      "매도 세금",
      c.sell_tax_rate > 0
        ? pct(c.sell_tax_rate)
        : market === "us"
          ? "양도세는 연 정산 (별도)"
          : "없음",
    ],
  ];
}

// ---------- 지표 카드 ----------

export interface Kpi {
  key: string;
  label: string;
  value: string;
  sub: string;
  tone: "plain" | "down" | "warn";
  compare?: string;
}

const money = (v: number | undefined, market: string) =>
  v == null ? DASH : market === "us" ? fmtUsd(v) : fmtKrw(v);

/** 비교 기준 행: 최근 3년, 없으면 최근 1년 (ADR 0033 §3). */
export function recentPeriod(periods: BacktestPeriod[] | undefined): BacktestPeriod | null {
  return periods?.find((p) => p.key === "3y") ?? periods?.find((p) => p.key === "1y") ?? null;
}

/** 지표 카드 5개. 비교 실행이 있으면 각 카드에 비교 값을 붙인다. */
export function kpis(run: BacktestRun, market: string, compare?: BacktestRun | null): Kpi[] {
  const m = run.metrics;
  const all = run.periods?.find((p) => p.key === "all");
  const bench = run.benchmark?.label ?? "벤치마크";
  const years = m.years ?? 0;
  const pct2 = (v: number | undefined | null) => (v == null ? DASH : fmtPct(v, { signed: false, digits: 2 }));
  const pctMdd = (v: number | undefined | null) => (v == null ? DASH : fmtPct(v, { digits: 1 }));
  const recent = recentPeriod(run.periods);
  const cm = compare?.metrics;
  const tag = compare ? `비교 #${compare.id}` : "";
  const perYear = (n: number | undefined, y: number | undefined) =>
    n == null || !y ? DASH : `연 ${fmtNumber(n / y, 1)}회`;
  return [
    {
      key: "cagr",
      label: "CAGR",
      value: pct2(m.cagr),
      sub: all?.bench_cagr != null ? `${bench} ${pct2(all.bench_cagr)}` : "벤치마크 없음",
      tone: "plain",
      compare: compare ? `${tag} ${pct2(cm?.cagr)}` : undefined,
    },
    {
      key: "mdd",
      label: "최대 낙폭 MDD",
      value: pctMdd(m.max_drawdown),
      sub: all?.bench_mdd != null ? `${bench} ${pctMdd(all.bench_mdd)}` : "벤치마크 없음",
      tone: "down",
      compare: compare ? `${tag} ${pctMdd(cm?.max_drawdown)}` : undefined,
    },
    {
      key: "sharpe",
      label: "샤프 비율",
      value: m.sharpe == null ? DASH : fmtNumber(m.sharpe, 2),
      sub: `${fmtNumber(years, 1)}년 · 봉 수익률 기준`,
      tone: "plain",
      compare: compare ? `${tag} ${cm?.sharpe == null ? DASH : fmtNumber(cm.sharpe, 2)}` : undefined,
    },
    {
      key: "trades",
      label: "매매 횟수",
      value: perYear(m.n_trades, m.years),
      sub: `총 ${m.n_trades ?? DASH}회 · 비용 ${money(m.total_costs, market)}`,
      tone: "plain",
      compare: compare ? `${tag} ${perYear(cm?.n_trades, cm?.years)}` : undefined,
    },
    {
      key: "recent",
      label: recent ? `${recent.label} · 벤치마크 대비` : "벤치마크 대비",
      value: recent?.excess == null ? DASH : `${fmtPct(recent.excess, { digits: 1 })}p`,
      sub:
        recent?.excess == null ? "비교할 벤치마크 없음" : recent.excess < 0 ? "연 초과수익 · 열위" : "연 초과수익 · 우위",
      tone: recent?.excess != null && recent.excess < 0 ? "warn" : "plain",
    },
  ];
}

// ---------- 자산 곡선 (로그) ----------

export const CHART = { w: 840, h: 300, left: 40, right: 820, top: 30, bottom: 280 } as const;
export const DD = { w: 840, h: 86, left: 40, right: 820, top: 6, bottom: 76 } as const;
// 640px 미만 화면용 틀: 같은 글자 크기가 폭 840 틀보다 두 배쯤 크게 보인다 (모바일에서 눈금이 4px로 줄던 것)
export const CHART_SM = { w: 420, h: 280, left: 34, right: 410, top: 26, bottom: 260 } as const;
export const DD_SM = { w: 420, h: 72, left: 34, right: 410, top: 6, bottom: 62 } as const;

export interface Frame {
  w: number;
  h: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

const tsOf = (p: EquityPt) => Date.parse(p.ts);

/** 시작 = 100으로 맞춘 곡선. */
export function normalize(points: EquityPt[] | undefined | null): EquityPt[] {
  if (!points?.length || !(points[0]!.v > 0)) return [];
  const base = points[0]!.v;
  return points.map((p) => ({ ts: p.ts, v: (p.v / base) * 100 }));
}

/** 시간 축: 모든 곡선과 홀드아웃 끝을 덮는 범위. */
export function timeDomain(series: (EquityPt[] | null | undefined)[], extraEnd?: string | null): [number, number] | null {
  const ts = series.flatMap((s) => (s ?? []).map(tsOf));
  if (extraEnd) ts.push(Date.parse(extraEnd));
  if (!ts.length) return null;
  return [Math.min(...ts), Math.max(...ts)];
}

const xOf = (t: number, [t0, t1]: [number, number], f: Frame) =>
  t1 === t0 ? f.left : f.left + ((t - t0) / (t1 - t0)) * (f.right - f.left);

/** 로그 눈금: 10배 넘게 벌어지면 1·2·5 × 10의 거듭제곱(많으면 10의 거듭제곱만), 아니면 로그 간격 4칸을 두 자리로 반올림. */
export function logTicks(min: number, max: number): number[] {
  if (!(min > 0) || !(max > 0)) return [];
  if (max / min >= 10) {
    const k0 = Math.floor(Math.log10(min));
    const k1 = Math.ceil(Math.log10(max));
    const nice: number[] = [];
    const decades: number[] = [];
    for (let k = k0; k <= k1; k++) {
      for (const m of [1, 2, 5]) {
        const v = m * 10 ** k;
        if (v >= min && v <= max) (m === 1 ? decades : nice).push(v);
      }
    }
    const all = [...nice, ...decades].sort((a, b) => a - b);
    return all.length <= 6 ? all : decades;
  }
  const lo = Math.log10(min);
  const hi = Math.log10(max);
  const out = new Set<number>();
  for (let i = 0; i <= 4; i++) {
    const v = 10 ** (lo + ((hi - lo) * i) / 4);
    const mag = 10 ** (Math.floor(Math.log10(v)) - 1);
    out.add(Math.round(v / mag) * mag);
  }
  return [...out].filter((v) => v >= min * 0.98 && v <= max * 1.02);
}

/** 눈금 이름: 1k·10k처럼 줄인다. */
export function tickLabel(v: number): string {
  if (v >= 1_000_000) return `${fmtNumber(v / 1_000_000, v % 1_000_000 ? 1 : 0)}M`;
  if (v >= 1_000) return `${fmtNumber(v / 1_000, v % 1_000 ? 1 : 0)}k`;
  return fmtNumber(v, v < 10 ? 1 : 0);
}

export interface EquityChart {
  main: string;
  bench: string | null;
  compare: string | null;
  yTicks: { y: number; label: string }[];
  xTicks: { x: number; label: string }[];
  holdout: { x: number; w: number } | null;
  shade: { x: number; w: number } | null;
  endLabel: { x: number; y: number; text: string } | null;
  benchEndLabel: { x: number; y: number; text: string } | null;
}

/** 연도 눈금 6개 안팎 (같은 해는 하나만). 하루 안의 곡선이면 날짜. */
function xTicks(dom: [number, number], f: Frame): { x: number; label: string }[] {
  const [t0, t1] = dom;
  const out: { x: number; label: string }[] = [];
  const spanDays = (t1 - t0) / 86_400_000;
  for (let i = 0; i <= 5; i++) {
    const t = t0 + ((t1 - t0) * i) / 5;
    const d = new Date(t);
    const label = spanDays > 730 ? String(d.getUTCFullYear()) : `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    if (!out.some((o) => o.label === label)) out.push({ x: xOf(t, dom, f), label });
  }
  return out;
}

const multiple = (v: number) => `×${fmtNumber(v / 100, v >= 1000 ? 0 : 2)}`;

/**
 * 로그 자산 곡선: 전략·벤치마크·비교를 시작 = 100으로 맞춰 같은 시간 축에 그린다.
 * holdout = 컷 ~ data_end(잠김), shade = 최근 구간이 벤치마크보다 못하면 그 구간 (ADR 0033 §3·§4).
 */
export function equityChart(
  run: Pick<BacktestRun, "equity" | "benchmark" | "holdout_cutoff" | "data_end" | "periods">,
  compare?: Pick<BacktestRun, "equity"> | null,
  f: Frame = CHART,
): EquityChart | null {
  const main = normalize(run.equity);
  if (main.length < 2) return null;
  const bench = normalize(run.benchmark?.points);
  const cmp = normalize(compare?.equity);
  const dom = timeDomain([main, bench, cmp], run.data_end);
  if (!dom) return null;
  const vs = [...main, ...bench, ...cmp].map((p) => p.v).filter((v) => v > 0);
  const lo = Math.min(...vs) * 0.95;
  const hi = Math.max(...vs) * 1.05;
  const y = (v: number) =>
    f.bottom - ((Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (f.bottom - f.top);
  const path = (ps: EquityPt[]) => (ps.length >= 2 ? ps.map((p) => `${xOf(tsOf(p), dom, f).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ") : null);
  const cutT = run.holdout_cutoff ? Date.parse(run.holdout_cutoff) : null;
  const endT = run.data_end ? Date.parse(run.data_end) : null;
  const holdout =
    cutT != null && endT != null && endT > cutT
      ? { x: xOf(cutT, dom, f), w: xOf(endT, dom, f) - xOf(cutT, dom, f) }
      : null;
  const recent = recentPeriod(run.periods);
  const shade =
    recent && recent.excess != null && recent.excess < 0
      ? { x: xOf(Date.parse(recent.start), dom, f), w: xOf(Date.parse(recent.end), dom, f) - xOf(Date.parse(recent.start), dom, f) }
      : null;
  const last = main[main.length - 1]!;
  const blast = bench.length ? bench[bench.length - 1]! : null;
  return {
    main: path(main)!,
    bench: path(bench),
    compare: path(cmp),
    yTicks: logTicks(lo, hi).map((v) => ({ y: y(v), label: tickLabel(v) })),
    xTicks: xTicks(dom, f),
    holdout,
    shade,
    endLabel: { x: xOf(tsOf(last), dom, f), y: y(last.v), text: multiple(last.v) },
    benchEndLabel: blast ? { x: xOf(tsOf(blast), dom, f), y: y(blast.v), text: multiple(blast.v) } : null,
  };
}

export interface DrawdownChart {
  main: string;
  bench: string | null;
  floor: number; // 가장 깊은 낙폭 (음수, 아래 눈금)
}

/** 낙폭 면적: 0%가 위, 가장 깊은 낙폭이 아래. 자산 곡선과 같은 시간 축을 쓴다. */
export function drawdownChart(
  run: Pick<BacktestRun, "equity" | "drawdown" | "benchmark" | "data_end">,
  f: Frame = DD,
): DrawdownChart | null {
  const dd = run.drawdown ?? [];
  if (dd.length < 2) return null;
  const bdd = run.benchmark?.drawdown ?? [];
  const dom = timeDomain([run.equity, run.benchmark?.points], run.data_end);
  if (!dom) return null;
  const floor = Math.min(-0.05, ...dd.map((p) => p.v), ...bdd.map((p) => p.v));
  const y = (v: number) => f.top + (v / floor) * (f.bottom - f.top);
  const poly = (ps: EquityPt[]) => {
    if (ps.length < 2) return null;
    const x0 = xOf(tsOf(ps[0]!), dom, f).toFixed(1);
    const xn = xOf(tsOf(ps[ps.length - 1]!), dom, f).toFixed(1);
    const body = ps.map((p) => `${xOf(tsOf(p), dom, f).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    return `${x0},${f.top} ${body} ${xn},${f.top}`;
  };
  return { main: poly(dd)!, bench: poly(bdd), floor };
}

// ---------- 구간 표 · 체크리스트 ----------

export interface PeriodRow {
  label: string;
  cagr: string;
  bench: string;
  mdd: string;
  excess: string;
  excessTone: "up" | "down" | "muted";
  compare?: string;
  locked: boolean;
}

const ym = (iso: string) => iso.slice(0, 7).replace("-", ".");

/** 구간별 성과 표: 서버 행 + 홀드아웃 행("잠김"). 비교가 있으면 비교 CAGR 칸. */
export function periodTable(run: BacktestRun, compare?: BacktestRun | null): PeriodRow[] {
  const pct = (v: number | null | undefined) => (v == null ? DASH : fmtPct(v, { signed: false, digits: 2 }));
  const rows: PeriodRow[] = (run.periods ?? []).map((p) => {
    const cp = compare?.periods?.find((c) => c.key === p.key);
    return {
      label: `${p.label} (${ym(p.start)} ~ ${ym(p.end)})`,
      cagr: pct(p.cagr),
      bench: pct(p.bench_cagr),
      mdd: fmtPct(p.mdd, { digits: 1 }),
      excess: p.excess == null ? DASH : `${fmtPct(p.excess, { digits: 1 })}p`,
      excessTone: p.excess == null ? "muted" : p.excess >= 0 ? "up" : "down",
      compare: compare ? pct(cp?.cagr) : undefined,
      locked: false,
    };
  });
  if (run.holdout_cutoff && !run.unlocked_holdout) {
    rows.push({
      label: `${ym(run.holdout_cutoff)} ~ ${run.data_end ? ym(run.data_end) : DASH} (홀드아웃)`,
      cagr: "잠김",
      bench: "잠김",
      mdd: "잠김",
      excess: DASH,
      excessTone: "muted",
      compare: compare ? "잠김" : undefined,
      locked: true,
    });
  }
  return rows;
}

export interface CheckItem {
  mark: "ok" | "warn" | "todo";
  text: string;
}

/** 실전 전환 전 확인: 비용 포함 · 관문 G1 · 시도 횟수 · 최근 열위 · 경고 · 홀드아웃. */
export function checklist(run: BacktestRun, strategy: StrategyView | undefined): CheckItem[] {
  const out: CheckItem[] = [];
  const cost = run.cost_model as Partial<CostModelView>;
  out.push(
    (cost.fee_rate ?? 0) > 0
      ? { mark: "ok", text: "비용·슬리피지 포함 결과" }
      : { mark: "warn", text: "비용 없이 계산한 결과" },
  );
  const g1 = strategy?.gate.g1;
  out.push(
    g1?.pass
      ? { mark: "ok", text: "공개 수치·기준 구현 ±20% 이내 (관문 G1)" }
      : { mark: "warn", text: `관문 G1 미통과${g1?.reason ? ` — ${g1.reason}` : ""}` },
  );
  const att = run.attempts;
  if (att) {
    out.push(
      att.distinct_attempts < att.warn_after
        ? { mark: "ok", text: `파라미터 시도 ${att.warn_after}회 미만 (${att.distinct_attempts}회)` }
        : { mark: "warn", text: `파라미터 시도 ${att.distinct_attempts}회 — 과최적화 주의` },
    );
  }
  const recent = recentPeriod(run.periods);
  if (recent?.excess != null && recent.excess < 0) {
    out.push({ mark: "warn", text: `${recent.label} 벤치마크 대비 연 ${fmtPct(recent.excess, { digits: 1 })}p 열위` });
  }
  if (run.warnings?.length) out.push({ mark: "warn", text: `백테스트 경고 ${run.warnings.length}건 (아래 목록)` });
  out.push({ mark: "todo", text: "홀드아웃은 실전 전환 직전 1회만 해제할 수 있습니다" });
  return out;
}
