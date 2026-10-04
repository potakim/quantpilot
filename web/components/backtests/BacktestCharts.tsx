"use client";

// 로그 자산 곡선 + 낙폭 (Backtest.dc.html 가운데 카드). 좌표 계산은 lib/backtest.ts, 여기서는 그리기만.
// 전략 = 보라, 벤치마크 = 회색(비용 없음), 비교 = 파랑 점선, 홀드아웃 = 주황 빗금(잠김), 최근 열위 = 연주황 배경.
// 640px 미만에서는 폭이 좁은 틀(CHART_SM·DD_SM)로 다시 그린다 — 같은 틀을 줄이면 눈금 글자가 4px까지 작아졌다.
import { CHART, CHART_SM, DD, DD_SM, drawdownChart, equityChart, type Frame } from "@/lib/backtest";
import { fmtPct } from "@/lib/format";
import type { BacktestRun } from "@/lib/types";

function Legend({ run, compare }: { run: BacktestRun; compare: BacktestRun | null }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      <li className="flex items-center gap-1.5">
        <span aria-hidden="true" className="h-[3px] w-3.5 bg-ai" />
        전략
      </li>
      {run.benchmark ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-[3px] w-3.5 bg-bench" />
          {run.benchmark.label} (비용 없음)
        </li>
      ) : null}
      {compare ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-0 w-3.5 border-t-2 border-dashed border-down" />
          비교 #{compare.id}
        </li>
      ) : null}
      {run.holdout_cutoff && !run.unlocked_holdout ? (
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2.5 w-3.5 border border-warn-line bg-warn-bg" />
          홀드아웃 (잠김)
        </li>
      ) : null}
    </ul>
  );
}

function EquitySvg({
  run,
  compare,
  f,
  label,
  className,
}: {
  run: BacktestRun;
  compare: BacktestRun | null;
  f: Frame;
  label: string;
  className: string;
}) {
  const eq = equityChart(run, compare, f);
  if (!eq) return null;
  const fs = f.w < 600 ? 2 : 0; // 좁은 틀은 글자를 2 키운다 (390px 화면에서 실제 9~10px)
  return (
    <svg viewBox={`0 0 ${f.w} ${f.h}`} className={className} role="img" aria-label={`${label}. 전략 끝 값 ${eq.endLabel?.text ?? ""}`}>
      <g className="stroke-line2">
        {eq.yTicks.map((t) => (
          <line key={t.label} x1={f.left} x2={f.right} y1={t.y} y2={t.y} />
        ))}
      </g>
      {eq.shade ? (
        <g>
          <rect x={eq.shade.x} y={f.top} width={eq.shade.w} height={f.bottom - f.top} className="fill-warn" fillOpacity={0.05} />
          <text x={eq.shade.x + 6} y={f.bottom - 8} className="fill-warn-ink" fontSize={10 + fs} fontWeight={600}>
            열위 구간
          </text>
        </g>
      ) : null}
      {eq.holdout ? (
        <rect
          x={eq.holdout.x}
          y={f.top}
          width={Math.max(eq.holdout.w, 2)}
          height={f.bottom - f.top}
          className="fill-warn-bg stroke-warn-line"
          strokeDasharray="3 2"
        />
      ) : null}
      <g className="fill-muted2" fontSize={10 + fs} fontFamily="var(--font-mono)">
        {eq.yTicks.map((t) => (
          <text key={t.label} x={0} y={t.y + 4}>
            {t.label}
          </text>
        ))}
        {eq.xTicks.map((t) => (
          <text key={t.label} x={t.x} y={f.h - 4} textAnchor="middle">
            {t.label}
          </text>
        ))}
      </g>
      {eq.bench ? <polyline fill="none" className="stroke-bench" strokeWidth={1.8} points={eq.bench} /> : null}
      {eq.compare ? <polyline fill="none" className="stroke-down" strokeWidth={1.8} strokeDasharray="5 3" points={eq.compare} /> : null}
      <polyline fill="none" className="stroke-ai" strokeWidth={2.4} strokeLinejoin="round" points={eq.main} />
      {eq.endLabel ? (
        <text x={Math.min(eq.endLabel.x, f.right) - 4} y={eq.endLabel.y - 8} textAnchor="end" className="fill-ink" fontSize={11 + fs} fontWeight={600}>
          전략 {eq.endLabel.text}
        </text>
      ) : null}
      {eq.benchEndLabel ? (
        <text x={Math.min(eq.benchEndLabel.x, f.right) - 4} y={eq.benchEndLabel.y + 16} textAnchor="end" className="fill-muted" fontSize={11 + fs}>
          벤치 {eq.benchEndLabel.text}
        </text>
      ) : null}
    </svg>
  );
}

function DrawdownSvg({ run, f, className }: { run: BacktestRun; f: Frame; className: string }) {
  const dd = drawdownChart(run, f);
  if (!dd) return null;
  return (
    <svg
      viewBox={`0 0 ${f.w} ${f.h}`}
      className={className}
      role="img"
      aria-label={`낙폭 비교. 전략 최대 ${fmtPct(run.metrics.max_drawdown ?? null, { digits: 1 })}`}
    >
      <line x1={f.left} x2={f.right} y1={f.top} y2={f.top} className="stroke-bench" />
      {dd.bench ? <polygon className="fill-bench" fillOpacity={0.35} points={dd.bench} /> : null}
      <polygon className="fill-ai" fillOpacity={0.55} points={dd.main} />
      <g className="fill-muted2" fontSize={f.w < 600 ? 12 : 10} fontFamily="var(--font-mono)">
        <text x={0} y={f.top + 4}>
          0%
        </text>
        <text x={0} y={f.bottom + 4}>
          {fmtPct(dd.floor, { digits: 0 })}
        </text>
      </g>
    </svg>
  );
}

export function BacktestCharts({ run, compare, title }: { run: BacktestRun; compare: BacktestRun | null; title: string }) {
  if (!equityChart(run, compare)) return <p className="text-xs text-muted">자산 곡선을 그릴 점이 부족합니다.</p>;
  const benchName = run.benchmark?.label ?? "벤치마크";
  const label = `자산 곡선, 로그 눈금, 시작 100 기준: ${title}${run.benchmark ? `, ${benchName}` : ""}`;
  const hasDd = drawdownChart(run) != null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold">자산 곡선 (로그) · 시작 = 100</h2>
        <Legend run={run} compare={compare} />
      </div>
      <EquitySvg run={run} compare={compare} f={CHART} label={label} className="hidden h-auto w-full sm:block" />
      <EquitySvg run={run} compare={compare} f={CHART_SM} label={label} className="h-auto w-full sm:hidden" />
      {hasDd ? (
        <>
          <h3 className="mt-1 text-xs font-semibold">낙폭 (drawdown)</h3>
          <DrawdownSvg run={run} f={DD} className="hidden h-auto w-full sm:block" />
          <DrawdownSvg run={run} f={DD_SM} className="h-auto w-full sm:hidden" />
        </>
      ) : null}
    </div>
  );
}
