import { Badge, cx } from "@/components/ui/primitives";
import { ConfidenceBar } from "@/components/ui/ConfidenceBar";
import { kstTime, shortSymbol } from "@/lib/format";
import { answerRows, judgmentResult, strategyLabel, verdictLabel } from "@/lib/labels";
import type { JudgmentRow } from "@/lib/types";

// 최근 AI 판단 카드 (Main.dc.html 우측 하단, Mobile.dc.html). 값은 /judgments 응답 그대로.
export function JudgmentCard({ j, compact = false }: { j: JudgmentRow; compact?: boolean }) {
  const res = judgmentResult(j.gate);
  const hold = j.gate === "hold";
  const verdicts = j.verdicts.map(verdictLabel);
  const disagree = verdicts.length === 2 && verdicts[0]!.approve !== verdicts[1]!.approve;
  const blockText = hold
    ? answerRows(j.answers)
        .filter((r) => j.blocks.includes(r.key))
        .map((r) => `${r.key} ${r.text}`)
        .join(" · ") || j.blocks.join(" · ")
    : "";
  const tone = res.tone === "ok" ? "text-ok" : res.tone === "warn" ? "text-warn" : "text-muted";
  return (
    <article
      className={cx(
        "flex flex-col gap-1.5",
        compact ? "rounded-[12px] border bg-bg2 px-3.5 py-3" : "rounded-block bg-bg3 p-3",
        compact && (hold ? "border-line" : "border-ai-line"),
      )}
    >
      {compact ? (
        <div className="flex justify-between text-xs">
          <span className="font-semibold">
            {shortSymbol(j.symbol)} · {strategyLabel(j.strategy)}
          </span>
          <span className={cx("font-semibold", res.tone === "ok" ? "text-ok-ink" : tone)}>{res.text}</span>
        </div>
      ) : (
        <>
          <div className="flex justify-between text-xs">
            <span className="num text-muted">{kstTime(j.ts)}</span>
            <span className={cx("font-semibold", tone)}>{hold ? "보류" : "진입"}</span>
          </div>
          <div className="text-[13px] font-semibold">
            {shortSymbol(j.symbol)} · {strategyLabel(j.strategy)}
          </div>
        </>
      )}
      {!hold || !compact ? <ConfidenceBar value={j.confidence} small={compact} /> : null}
      {verdicts.length && !compact ? (
        <div className="flex flex-wrap gap-1.5">
          {verdicts.map((v, i) => (
            <Badge key={i} tone={v.approve ? "ok" : "warn2"}>
              {v.name} {v.text}
            </Badge>
          ))}
        </div>
      ) : null}
      {compact && verdicts.length ? (
        <div className="text-[11px] text-muted">
          {verdicts.map((v) => `${v.name} ${v.approve ? "✓ 승인" : "✗ 보류"}`).join(" · ")}
        </div>
      ) : null}
      {hold && blockText ? <div className="text-xs text-muted">{blockText} → 규칙상 진입 차단</div> : null}
      {disagree ? <div className="text-xs text-muted">두 모델 불일치 → 규칙상 관망</div> : null}
    </article>
  );
}
