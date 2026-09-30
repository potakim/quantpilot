"use client";

// AI 판단 로그 (AILog.dc.html): 보정 카드 4개 → 필터 + 판단 표(선택 행 --ai-bg) + 우측 상세.
// 표 하단 "규칙 미충족 n건" 문구는 API가 그 수를 주지 않으므로 넣지 않는다 (지어내지 않는다).
import { useState } from "react";
import { CalibrationCards } from "@/components/judgments/CalibrationCards";
import { JudgmentDetailPanel } from "@/components/judgments/JudgmentDetailPanel";
import { Badge, CardSkeleton, EmptyNote, ErrorNote, Segmented, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { fmtNumber, fmtPct, kstDayStartIso, kstRelTime, shortSymbol, toneOf } from "@/lib/format";
import { MARKET_LABEL, answerRows, judgmentResult, strategyLabel, verdictLabel } from "@/lib/labels";
import { useJudgments, useStrategies } from "@/lib/queries";
import type { JudgmentRow } from "@/lib/types";

type Period = "today" | "7d" | "4w";
const PERIOD_DAYS: Record<Period, number> = { today: 0, "7d": 6, "4w": 27 };
const PERIOD_LABEL: Record<Period, string> = { today: "오늘", "7d": "7일", "4w": "4주" };
const OUTCOMES: [string, string][] = [
  ["pending", "대기"],
  ["filled", "체결"],
  ["judged_hold", "판단 보류"],
  ["expired", "만료·거부"],
];
const TONE = { up: "text-up", down: "text-down", muted: "text-muted" } as const;
const RES = { ok: "text-ok-ink", warn: "text-warn", muted: "text-ink2", ai: "text-ai-ink", up: "text-up" } as const;

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="flex h-[30px] items-center gap-1 rounded-[7px] border border-line px-2 text-xs text-muted">
      {label}:
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-xs text-ink2 outline-offset-2"
      >
        <option value="" className="bg-bg2">
          전체
        </option>
        {options.map(([v, l]) => (
          <option key={v} value={v} className="bg-bg2">
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function ModelCell({ j }: { j: JudgmentRow }) {
  const rows = answerRows(j.answers);
  const regime = rows.find((r) => r.key === "regime");
  const news = rows.find((r) => r.key === "news_risk");
  if (!regime && !news) return <span className="text-muted">{j.provider}</span>;
  return (
    <span className="text-ink2">
      {regime?.text ?? "—"} · <span className={news?.tone === "warn" ? "text-warn" : ""}>{news?.text ?? "—"}</span>
    </span>
  );
}

function LlmCell({ j }: { j: JudgmentRow }) {
  if (!j.verdicts.length) {
    return (
      <span className="text-[11px] text-muted">호출 안 함{j.blocks.length ? ` (${j.blocks.join(", ")} 차단)` : ""}</span>
    );
  }
  return (
    <span className="flex flex-wrap gap-1">
      {j.verdicts.map((v, i) => {
        const l = verdictLabel(v);
        return (
          <Badge key={i} tone={l.approve ? "ok" : "warn2"} className="px-1.5 text-[11px]">
            {l.short} {l.text}
          </Badge>
        );
      })}
    </span>
  );
}

export function JudgmentLog() {
  const [period, setPeriod] = useState<Period>("today");
  const [market, setMarket] = useState("");
  const [strategy, setStrategy] = useState("");
  const [outcome, setOutcome] = useState("");
  const [limit, setLimit] = useState(50);
  const [picked, setPicked] = useState<number | null>(null);
  const [from] = useState(() => ({
    today: kstDayStartIso(new Date(), 0),
    "7d": kstDayStartIso(new Date(), PERIOD_DAYS["7d"]),
    "4w": kstDayStartIso(new Date(), PERIOD_DAYS["4w"]),
  }));
  const strategies = useStrategies();
  const q = useJudgments({ from: from[period], market, strategy, outcome, limit });
  const items = q.data?.items ?? [];
  const selected = picked ?? items[0]?.id ?? null;
  const entries = items.filter((j) => j.gate !== "hold").length;
  const now = new Date();

  return (
    <div className="flex flex-col gap-3.5 px-5 py-5 md:px-6">
      <h1 className="sr-only">AI 판단 로그</h1>
      <CalibrationCards />
      <div className="flex min-h-0 flex-grow flex-col gap-3.5 xl:flex-row">
        <section aria-label="판단 목록" className="flex min-w-0 flex-grow flex-col overflow-hidden rounded-card border border-line bg-bg2">
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <Segmented
              label="기간"
              framed={false}
              value={period}
              onChange={(v) => {
                setPeriod(v);
                setPicked(null);
              }}
              options={(Object.keys(PERIOD_LABEL) as Period[]).map((p) => ({ value: p, label: PERIOD_LABEL[p] }))}
            />
            <span aria-hidden="true" className="mx-1 h-5 w-px bg-line" />
            <FilterSelect label="시장" value={market} onChange={setMarket} options={Object.entries(MARKET_LABEL)} />
            <FilterSelect
              label="전략"
              value={strategy}
              onChange={setStrategy}
              options={(strategies.data ?? []).map((s) => [s.name, strategyLabel(s.name)])}
            />
            <FilterSelect label="결과" value={outcome} onChange={setOutcome} options={OUTCOMES} />
            <span className="flex-grow" />
            <span className="text-xs text-muted" aria-live="polite">
              {PERIOD_LABEL[period]} {items.length}
              {q.data?.next_cursor != null ? "+" : ""}건 · 진입 {entries} · 보류 {items.length - entries}
            </span>
          </div>
          {q.isLoading ? <CardSkeleton lines={6} className="m-4 border-0 p-0" /> : null}
          {q.isError ? (
            <div className="p-4">
              <ErrorNote>{reasonText(q.error)}</ErrorNote>
            </div>
          ) : null}
          {!q.isLoading && !items.length && !q.isError ? (
            <div className="p-4">
              <EmptyNote>이 조건의 판단이 없습니다</EmptyNote>
            </div>
          ) : null}
          {items.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-xs">
                <caption className="sr-only">AI 판단 목록 — 행을 선택하면 오른쪽에 상세가 나옵니다</caption>
                <thead>
                  <tr className="border-b border-bg3 text-left text-[11px] text-muted">
                    {["시각", "종목 · 전략", "판단 모델 (regime · news_risk)", "확신도", "LLM 합의", "결과", "24h 후"].map((h) => (
                      <th key={h} scope="col" className="px-2.5 py-2 font-normal first:pl-4">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((j, i) => {
                    const on = j.id === selected;
                    const res = judgmentResult(j.gate);
                    return (
                      <tr key={j.id} className={cx(on ? "bg-ai-bg" : "hover:bg-bg3", i > 0 && !on && "border-t border-bg3")}>
                        <td className={cx("num px-2.5 py-2.5 pl-4", on ? "text-ai-ink" : "text-muted")}>{kstRelTime(j.ts, now)}</td>
                        <td className="px-2.5 py-2.5 font-semibold">
                          <button
                            type="button"
                            aria-pressed={on}
                            onClick={() => setPicked(j.id)}
                            className="text-left font-semibold text-ink"
                          >
                            {shortSymbol(j.symbol)} · {strategyLabel(j.strategy)}
                          </button>
                        </td>
                        <td className="px-2.5 py-2.5">
                          <ModelCell j={j} />
                        </td>
                        <td className="num px-2.5 py-2.5">{fmtNumber(j.confidence, 2)}</td>
                        <td className="px-2.5 py-2.5">
                          <LlmCell j={j} />
                        </td>
                        <td className={cx("px-2.5 py-2.5 font-semibold", RES[res.tone])}>{res.text}</td>
                        <td className={cx("num px-2.5 py-2.5", j.realized_ret_24h == null ? "text-muted" : TONE[toneOf(j.realized_ret_24h)])}>
                          {j.realized_ret_24h == null ? "대기 중" : fmtPct(j.realized_ret_24h)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {q.data?.next_cursor != null && limit < 500 ? (
            <div className="mt-auto flex justify-end border-t border-line px-4 py-2.5 text-xs">
              <button type="button" onClick={() => setLimit((l) => Math.min(500, l + 50))} className="font-medium text-ai">
                더 보기 →
              </button>
            </div>
          ) : null}
        </section>
        <JudgmentDetailPanel key={selected ?? "none"} id={selected} />
      </div>
    </div>
  );
}
