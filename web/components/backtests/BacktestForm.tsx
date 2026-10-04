"use client";

// 백테스트 설정 (Backtest.dc.html 왼쪽 300px). 파라미터는 전략 설정 화면에 저장된 값으로 돌린다 (ADR 0033 §6).
// 비용 모델은 읽기 전용(불변식 #4), 홀드아웃은 켜진 채 잠김(불변식 #5 — 해제는 실전 전환 직전 1회).
import Link from "next/link";
import { Button } from "@/components/ui/controls";
import { Card, ErrorNote } from "@/components/ui/primitives";
import { SOURCE_LABEL, costRows, sourcesFor } from "@/lib/backtest";
import { STRATEGY_TITLE } from "@/lib/labels";
import type { StrategyView } from "@/lib/types";

export interface RunForm {
  strategy: string;
  source: string;
  start: string; // YYYY-MM 또는 ""
  end: string;
}

const FIELD = "h-[38px] rounded-btn border border-line bg-bg0 px-2.5 text-[13px] text-ink";

export function BacktestForm({
  strategies,
  form,
  onChange,
  onRun,
  running,
  progress,
  error,
}: {
  strategies: StrategyView[];
  form: RunForm;
  onChange: (f: RunForm) => void;
  onRun: () => void;
  running: boolean;
  progress: number | null;
  error: string | null;
}) {
  const s = strategies.find((x) => x.name === form.strategy);
  const sources = s ? sourcesFor(s) : ["synthetic"];
  const attempts = s?.gate.g1?.evidence?.distinct_attempts;
  const set = (patch: Partial<RunForm>) => onChange({ ...form, ...patch });

  return (
    <Card as="aside" aria-label="백테스트 설정" className="flex w-full shrink-0 flex-col gap-3 px-[18px] py-4 lg:w-[300px]">
      <h2 className="text-sm font-semibold">설정</h2>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        전략
        <select
          className={FIELD}
          value={form.strategy}
          onChange={(e) => {
            const next = strategies.find((x) => x.name === e.currentTarget.value);
            set({ strategy: e.currentTarget.value, source: next ? sourcesFor(next)[0]! : "synthetic" });
          }}
        >
          {strategies.map((x) => (
            <option key={x.name} value={x.name}>
              {STRATEGY_TITLE[x.name] ?? x.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        데이터
        <select className={FIELD} value={form.source} onChange={(e) => set({ source: e.currentTarget.value })}>
          {sources.map((src) => (
            <option key={src} value={src}>
              {SOURCE_LABEL[src] ?? src}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-col gap-1.5 text-xs text-muted">
        자산
        <p className="num rounded-btn border border-line bg-bg0 px-2.5 py-2 text-xs text-ink">
          {s ? s.symbols.join(" · ") : "—"}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          시작
          <input
            type="month"
            className={`${FIELD} num`}
            value={form.start}
            onChange={(e) => set({ start: e.currentTarget.value })}
            aria-describedby="bt-period-note"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          종료
          <input
            type="month"
            className={`${FIELD} num`}
            value={form.end}
            onChange={(e) => set({ end: e.currentTarget.value })}
            aria-describedby="bt-period-note"
          />
        </label>
      </div>
      <p id="bt-period-note" className="-mt-1.5 text-[11px] text-muted">
        {form.source === "upbit"
          ? "비워 두면 최근 약 1,000일, 시작을 고르면 그날부터 받습니다"
          : form.source === "synthetic"
            ? "합성 데이터는 2017년부터 만든 연습용 시세입니다"
            : "비워 두면 받을 수 있는 처음·끝까지 씁니다"}
      </p>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-muted">파라미터</span>
        <Link href="/strategies" className="text-xs">
          전략 설정 값 사용 →
        </Link>
      </div>

      <div className="h-px bg-line" />
      <h3 className="text-xs font-semibold">비용 모델 (필수)</h3>
      <dl className="flex flex-col gap-1.5 text-xs">
        {costRows(s?.cost_model, s?.market ?? "").map(([k, v]) => (
          <div key={k} className="flex justify-between gap-2 rounded-[8px] bg-bg3 px-2.5 py-2">
            <dt className="text-ink2">{k}</dt>
            <dd className="num">{v}</dd>
          </div>
        ))}
      </dl>
      <label className="flex items-center justify-between rounded-[8px] bg-bg3 px-2.5 py-2 text-xs">
        <span className="text-ink2">홀드아웃 · 최근 12개월 잠금</span>
        <input type="checkbox" checked disabled readOnly className="h-4 w-4 accent-ai" aria-describedby="bt-holdout-note" />
      </label>
      <p id="bt-holdout-note" className="-mt-1.5 text-[11px] text-muted">
        해제는 실전 전환 직전 1회만 할 수 있어 여기서는 풀지 않습니다
      </p>
      <div className="flex justify-between gap-2 rounded-[8px] bg-bg3 px-2.5 py-2 text-xs">
        <span className="text-ink2">상장폐지 종목</span>
        <span className="text-muted">고정 종목 목록이라 해당 없음</span>
      </div>
      <div className="flex flex-col gap-1 rounded-block bg-warn-bg px-3 py-2.5 text-xs">
        <div className="flex justify-between">
          <span className="font-semibold text-warn-ink">파라미터 시도 횟수</span>
          <span className="num text-warn-ink">{attempts != null ? `${attempts} / 7` : "—"}</span>
        </div>
        <p className="leading-normal text-ink2">
          7회를 넘기면 과최적화 경고가 결과에 표시됩니다 (Bailey · López de Prado 기준)
        </p>
      </div>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <Button variant="primary" className="mt-auto h-[42px]" disabled={running || !s} onClick={onRun}>
        {running ? `실행 중… ${progress != null ? Math.round(progress * 100) : 0}%` : "백테스트 실행"}
      </Button>
    </Card>
  );
}
