"use client";

// AI 판단 패널 (Trade.dc.html 우측 위, --ai-line 테두리): 확신도·게이팅 결과·원자 질문 바·LLM 2모델·리스크 게이트.
// 판단 모델은 확률·확신도만 준다 (불변식 #7). 리스크 게이트 값은 GET /settings의 코드 상수(locked) 그대로.
import { IconSparkle } from "@/components/ui/Icons";
import { EmptyNote, cx } from "@/components/ui/primitives";
import { fmtNumber, fmtPct, kstTime, shortSymbol } from "@/lib/format";
import { answerRows, gateLabel, verdictLabel } from "@/lib/labels";
import type { JudgmentRow, RiskRulesView } from "@/lib/types";

const BAR = { ai: "bg-ai", ok: "bg-ok", warn: "bg-warn", muted: "bg-muted", up: "bg-up" } as const;
const TXT = { ok: "text-ok-ink", warn: "text-warn", muted: "text-muted", ai: "text-ai-ink", up: "text-up" } as const;

export function AiPanel({ symbol, judgment, rules }: { symbol: string; judgment: JudgmentRow | null; rules: RiskRulesView | null }) {
  const g = gateLabel(judgment?.gate);
  const rows = answerRows(judgment?.answers);
  const verdicts = judgment?.verdicts ?? [];
  return (
    <section aria-labelledby="ai-title" className="flex flex-col gap-3 rounded-card border border-ai-line-2 bg-bg2 p-4">
      <div className="flex items-center justify-between">
        <h2 id="ai-title" className="flex items-center gap-2 text-sm font-semibold">
          <IconSparkle size={16} className="text-ai" />
          AI 판단 · {shortSymbol(symbol)}
        </h2>
        {judgment ? (
          <span className="num text-[11px] text-muted">
            {kstTime(judgment.ts)}
            {judgment.latency_ms != null ? ` · ${Math.round(judgment.latency_ms)}ms` : ""}
          </span>
        ) : null}
      </div>
      {!judgment ? (
        <EmptyNote>이 종목의 판단 기록이 아직 없습니다. 전략 신호가 나면 판단 모델이 호출됩니다.</EmptyNote>
      ) : (
        <>
          <div className="flex items-center justify-between rounded-block bg-ai-bg px-3 py-2.5">
            <div className="flex flex-col gap-0.5">
              <span className="text-[11px] text-ai-ink">확신도 (confidence)</span>
              <span className="num text-[22px] font-bold">{fmtNumber(judgment.confidence, 2)}</span>
            </div>
            <div className="flex flex-col items-end gap-0.5">
              <span className="text-[11px] text-ai-ink">게이팅 결과</span>
              <span className={cx("text-[13px] font-semibold", TXT[g.tone])}>{g.text}</span>
            </div>
          </div>
          <ul className="flex flex-col gap-2 text-xs">
            {rows.map((r) => (
              <li key={r.key} className="flex items-center gap-2">
                <span className="w-24 shrink-0 truncate text-muted">{r.key}</span>
                <span aria-hidden="true" className="h-1.5 flex-grow overflow-hidden rounded-[3px] bg-bg0">
                  <span className={cx("block h-1.5", BAR[r.tone])} style={{ width: `${r.width * 100}%` }} />
                </span>
                <span className="num w-[92px] shrink-0 text-right">{r.text}</span>
              </li>
            ))}
          </ul>
          <div className="h-px bg-line" />
          <div className="text-xs text-muted">LLM 합의 (2/2 필요)</div>
          {verdicts.length ? (
            <ul className="flex flex-col gap-2">
              {verdicts.map((v, i) => {
                const l = verdictLabel(v);
                return (
                  <li key={v.id ?? i} className="flex flex-col gap-1 rounded-block bg-bg3 px-3 py-2.5">
                    <div className="flex justify-between text-xs">
                      <span className="font-semibold">{v.model}</span>
                      <span className={cx("font-semibold", l.approve ? "text-ok-ink" : "text-warn")}>{l.text}</span>
                    </div>
                    {v.reason ? <p className="text-xs leading-normal text-ink2">{v.reason}</p> : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-muted">
              호출 안 함{judgment.blocks.length ? ` (${judgment.blocks.join(", ")} 차단)` : ""}
            </p>
          )}
        </>
      )}
      <div className="h-px bg-line" />
      <div className="text-xs text-muted">리스크 게이트 (코드, 변경 불가)</div>
      {rules ? (
        <ul className="grid grid-cols-2 gap-1.5 text-xs">
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ok">✓</span>거래당 손실 {fmtPct(rules.max_loss_per_trade, { signed: false, digits: 0 })}
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ok">✓</span>월 한도 {fmtPct(rules.monthly_loss_limit, { signed: false, digits: 0 })}
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ok">✓</span>종목 비중 {fmtPct(rules.max_symbol_weight, { signed: false, digits: 0 })}
          </li>
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ok">✓</span>자전거래 없음
          </li>
        </ul>
      ) : (
        <p className="text-xs text-muted">규칙을 불러오는 중…</p>
      )}
    </section>
  );
}
