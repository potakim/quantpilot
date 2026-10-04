"use client";

// 백테스트 결과 (Backtest.dc.html 오른쪽): 제목 · 실행 기록 · 비교 추가 · CSV → 지표 카드 5개 → 자산 곡선·낙폭 →
// 구간별 성과 표 + 실전 전환 전 확인. 숫자 정의는 ADR 0033, 계산은 lib/backtest.ts.
import { BacktestCharts } from "@/components/backtests/BacktestCharts";
import { Button, buttonClass } from "@/components/ui/controls";
import { Card, cx } from "@/components/ui/primitives";
import { SOURCE_LABEL, checklist, kpis, periodTable } from "@/lib/backtest";
import { fmtPct, kstRelTime } from "@/lib/format";
import { STRATEGY_TITLE, strategyLabel } from "@/lib/labels";
import type { BacktestListItem, BacktestRun, StrategyView } from "@/lib/types";

const KPI_VALUE = { plain: "text-ink", down: "text-down", warn: "text-warn-ink" } as const;
const MARK = {
  ok: { sym: "✓", cls: "text-ok", text: "text-ink" },
  warn: { sym: "!", cls: "text-warn", text: "text-ink2" },
  todo: { sym: "○", cls: "text-muted2", text: "text-muted" },
} as const;

const ym = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : "—");

/** 실행 기록 한 줄 이름. */
export function runLabel(r: Pick<BacktestListItem, "id" | "ts" | "strategy" | "source" | "metrics">): string {
  const cagr = r.metrics?.cagr;
  return `#${r.id} · ${strategyLabel(r.strategy)} · ${SOURCE_LABEL[r.source] ?? r.source}${
    cagr != null ? ` · CAGR ${fmtPct(cagr, { signed: false, digits: 1 })}` : ""
  }${r.ts ? ` · ${kstRelTime(r.ts)}` : ""}`;
}

export function BacktestResult({
  run,
  strategy,
  history,
  onSelect,
  compare,
  compareChoices,
  onCompare,
}: {
  run: BacktestRun;
  strategy: StrategyView | undefined;
  history: BacktestListItem[];
  onSelect: (id: number) => void;
  compare: BacktestRun | null;
  compareChoices: BacktestListItem[];
  onCompare: (id: number | null) => void;
}) {
  const title = STRATEGY_TITLE[run.strategy] ?? run.strategy;
  const market = strategy?.market ?? "upbit";
  const cards = kpis(run, market, compare);
  const rows = periodTable(run, compare);
  const checks = checklist(run, strategy);
  const att = run.attempts;

  return (
    <section aria-label="백테스트 결과" className="flex min-w-0 flex-grow flex-col gap-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-[15px] font-semibold">
            {title} · {ym(run.period_start)} ~ {ym(run.period_end)} · 비용 포함
          </h1>
          <p className="text-xs text-muted">
            {SOURCE_LABEL[run.source] ?? run.source}
            {run.holdout_cutoff ? ` · ${run.holdout_cutoff} 이후 홀드아웃 잠김` : ""}
            {att ? ` · 파라미터 시도 ${att.distinct_attempts} / ${att.warn_after}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="bt-history">
            실행 기록
          </label>
          <select
            id="bt-history"
            className="h-8 max-w-[260px] rounded-btn border border-line bg-bg0 px-2 text-xs text-ink"
            value={run.id}
            onChange={(e) => onSelect(Number(e.currentTarget.value))}
          >
            {history.map((h) => (
              <option key={h.id} value={h.id}>
                {runLabel(h)}
              </option>
            ))}
          </select>
          <CompareControl compare={compare} choices={compareChoices} onCompare={onCompare} />
          <a
            href={`/api/qp/backtests/${run.id}/report.csv`}
            download={`backtest-${run.id}.csv`}
            className={buttonClass("ghost", "h-8 px-3")}
          >
            리포트 내보내기 (체결 CSV)
          </a>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {cards.map((k) => (
          <Card
            key={k.key}
            line={k.tone === "warn" ? "border-warn-line" : undefined}
            className="flex flex-col gap-1 rounded-[12px] px-3.5 py-3"
          >
            <span className={cx("text-[11px]", k.tone === "warn" ? "text-warn-ink" : "text-muted")}>{k.label}</span>
            <span className={cx("num text-xl font-bold", KPI_VALUE[k.tone])}>{k.value}</span>
            <span className="text-[11px] text-muted">{k.sub}</span>
            {k.compare ? <span className="text-[11px] text-down">{k.compare}</span> : null}
          </Card>
        ))}
      </div>

      <Card className="px-[18px] py-4">
        <BacktestCharts run={run} compare={compare} title={title} />
      </Card>

      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card className="flex flex-col gap-2 overflow-x-auto px-[18px] py-3.5">
          <h2 className="text-[13px] font-semibold">구간별 성과</h2>
          <table className="w-full min-w-[460px] border-collapse text-xs">
            <caption className="sr-only">구간별 전략·벤치마크 연수익률, 최대 낙폭, 초과수익</caption>
            <thead>
              <tr className="border-b border-line text-left text-[11px] text-muted">
                <th scope="col" className="pb-1.5 font-normal">
                  구간
                </th>
                <th scope="col" className="pb-1.5 font-normal">
                  전략 CAGR
                </th>
                <th scope="col" className="pb-1.5 font-normal">
                  벤치 CAGR
                </th>
                <th scope="col" className="pb-1.5 font-normal">
                  전략 MDD
                </th>
                <th scope="col" className="pb-1.5 font-normal">
                  초과수익
                </th>
                {compare ? (
                  <th scope="col" className="pb-1.5 font-normal text-down">
                    비교 CAGR
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label} className={cx("[&>*]:py-1.5", r.locked && "text-muted")}>
                  <th scope="row" className="pr-2 text-left font-normal">
                    {r.label}
                  </th>
                  <td className="num">{r.cagr}</td>
                  <td className="num">{r.bench}</td>
                  <td className="num">{r.mdd}</td>
                  <td className={cx("num", r.excessTone === "up" ? "text-up" : r.excessTone === "down" ? "text-down" : "")}>
                    {r.excess}
                  </td>
                  {compare ? <td className="num">{r.compare}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length ? <p className="text-xs text-muted">구간 성과가 없는 실행입니다 (ADR 0033 이전 결과) — 다시 실행하면 채워집니다.</p> : null}
        </Card>
        <Card className="flex flex-col gap-2 px-[18px] py-3.5">
          <h2 className="text-[13px] font-semibold">실전 전환 전 확인</h2>
          <ul className="flex flex-col gap-1.5 text-xs">
            {checks.map((c) => (
              <li key={c.text} className="flex items-start gap-2">
                <span aria-hidden="true" className={cx("w-3 shrink-0 text-center", MARK[c.mark].cls)}>
                  {MARK[c.mark].sym}
                </span>
                <span className={MARK[c.mark].text}>
                  <span className="sr-only">{c.mark === "ok" ? "충족: " : c.mark === "warn" ? "주의: " : "안내: "}</span>
                  {c.text}
                </span>
              </li>
            ))}
          </ul>
          {run.warnings?.length ? (
            <details className="text-[11px] text-muted">
              <summary className="cursor-pointer">경고 {run.warnings.length}건 보기</summary>
              <ul className="mt-1 flex max-h-40 flex-col gap-0.5 overflow-y-auto">
                {run.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </Card>
      </div>
    </section>
  );
}

function CompareControl({
  compare,
  choices,
  onCompare,
}: {
  compare: BacktestRun | null;
  choices: BacktestListItem[];
  onCompare: (id: number | null) => void;
}) {
  if (compare) {
    return (
      <Button variant="ghost" className="h-8 px-3 text-down" onClick={() => onCompare(null)} aria-label={`비교 #${compare.id} 빼기`}>
        비교 #{compare.id} ✕
      </Button>
    );
  }
  return (
    <>
      <label className="sr-only" htmlFor="bt-compare">
        비교 추가
      </label>
      <select
        id="bt-compare"
        className="h-8 max-w-[200px] rounded-btn border border-line bg-transparent px-2 text-xs text-muted"
        value=""
        disabled={!choices.length}
        onChange={(e) => e.currentTarget.value && onCompare(Number(e.currentTarget.value))}
      >
        <option value="">{choices.length ? "비교 추가…" : "비교할 실행 없음"}</option>
        {choices.map((h) => (
          <option key={h.id} value={h.id}>
            {runLabel(h)}
          </option>
        ))}
      </select>
    </>
  );
}
