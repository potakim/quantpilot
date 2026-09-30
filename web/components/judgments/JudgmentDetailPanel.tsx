"use client";

// 판단 상세 (AILog.dc.html 우측 380px): state 원문 · 원자 질문 · confidence → 게이트 · LLM 이유 · "이 판단에 대해 물어보기".
// 물어보기는 POST /judgments/{id}/ask — 기록만 근거로 설명한다 (수량·가격을 묻지 않는다, 불변식 #7).
import { useMutation } from "@tanstack/react-query";
import { useRef } from "react";
import { Badge, CardSkeleton, EmptyNote, ErrorNote, cx } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { fmtNumber, fmtUsd, kstRelTime, shortSymbol } from "@/lib/format";
import { answerRows, gateLabel, judgmentResult, strategyLabel, verdictLabel } from "@/lib/labels";
import { useJudgment } from "@/lib/queries";

export function JudgmentDetailPanel({ id }: { id: number | null }) {
  const q = useJudgment(id);
  const input = useRef<HTMLInputElement>(null);
  const ask = useMutation({
    mutationFn: (question: string) =>
      apiFetch<{ answer: string; cost_usd: number }>(`/judgments/${id}/ask`, { method: "POST", json: { question } }),
  });
  const d = q.data;

  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const text = input.current?.value.trim() ?? "";
    if (!text || id === null) return;
    ask.mutate(text);
  }

  return (
    <aside
      aria-label="판단 상세"
      className="flex w-full shrink-0 flex-col gap-3 overflow-y-auto rounded-card border border-ai-line-2 bg-bg2 p-4 xl:w-[380px]"
    >
      {id === null ? <EmptyNote>왼쪽 표에서 판단을 고르세요</EmptyNote> : null}
      {q.isLoading ? <CardSkeleton lines={6} className="border-0 p-0" /> : null}
      {q.isError ? <ErrorNote>{reasonText(q.error)}</ErrorNote> : null}
      {d ? (
        <>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">
              {shortSymbol(d.symbol)} · {strategyLabel(d.strategy)} · {kstRelTime(d.ts)}
            </h2>
            <Badge tone={judgmentResult(d.gate).tone === "ok" ? "ok" : "warn"}>{judgmentResult(d.gate).text}</Badge>
          </div>
          <div className="text-xs text-muted">state (코드가 만든 입력)</div>
          <pre className="num max-h-[180px] overflow-auto whitespace-pre-wrap rounded-block border border-line bg-bg0 px-3 py-2.5 text-[11px] leading-relaxed text-ink2">
            {typeof d.state?.text === "string" ? d.state.text : JSON.stringify(d.state, null, 2)}
          </pre>
          <div className="text-xs text-muted">
            원자 질문 {Object.keys(d.answers ?? {}).length}개 · 한 호출
            {d.latency_ms != null ? ` · ${Math.round(d.latency_ms)}ms` : ""}
            {d.cost_usd != null ? ` · ${fmtUsd(d.cost_usd, 5)}` : ""}
          </div>
          <ul className="flex flex-col gap-[5px] text-xs">
            {answerRows(d.answers).map((r) => (
              <li key={r.key} className="flex justify-between gap-3 rounded-[8px] bg-bg3 px-2.5 py-1.5">
                <span className="text-ink2">{r.key}</span>
                <span className={cx("num text-right", r.tone === "warn" && "text-warn")}>{r.full}</span>
              </li>
            ))}
          </ul>
          <div className="flex justify-between rounded-block bg-ai-bg px-3 py-2.5 text-xs">
            <span className="text-ai-ink">confidence {fmtNumber(d.confidence, 2)} → 게이트 {d.gate}</span>
            <span className="font-semibold">{gateLabel(d.gate).text}</span>
          </div>
          <div className="text-xs text-muted">LLM 합의</div>
          {d.verdicts.length ? (
            <ul className="flex flex-col gap-1.5 text-xs">
              {d.verdicts.map((v, i) => {
                const l = verdictLabel(v);
                return (
                  <li key={v.id ?? i} className="rounded-[8px] bg-bg3 px-2.5 py-2 leading-normal">
                    <span className="font-semibold">{l.name}</span>{" "}
                    <span className={cx("font-semibold", l.approve ? "text-ok-ink" : "text-warn")}>{l.text}</span>
                    {v.reason ? ` · ${v.reason}` : ""}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-muted">호출 안 함{d.blocks.length ? ` (${d.blocks.join(", ")} 차단)` : ""}</p>
          )}
          {ask.data ? (
            <div role="status" className="rounded-block border border-ai-line bg-bg3 px-3 py-2.5 text-xs leading-relaxed text-ink2">
              {ask.data.answer}
              <div className="num mt-1 text-[11px] text-muted">비용 {fmtUsd(ask.data.cost_usd, 4)}</div>
            </div>
          ) : null}
          {ask.isError ? <ErrorNote>{reasonText(ask.error)}</ErrorNote> : null}
          <form onSubmit={submit} className="mt-auto flex gap-2">
            <label htmlFor="ask-input" className="sr-only">
              AI에게 질문
            </label>
            <input
              id="ask-input"
              ref={input}
              type="text"
              maxLength={500}
              autoComplete="off"
              placeholder="이 판단에 대해 물어보기 · 예: 왜 절반만 샀어?"
              className="h-[38px] min-w-0 flex-grow rounded-[9px] border border-line bg-bg0 px-3 text-xs text-ink placeholder:text-muted"
            />
            <button
              type="submit"
              disabled={ask.isPending}
              className="h-[38px] rounded-[9px] bg-ai px-3.5 text-xs font-bold text-bg0 disabled:opacity-60"
            >
              {ask.isPending ? "…" : "질문"}
            </button>
          </form>
        </>
      ) : null}
    </aside>
  );
}
