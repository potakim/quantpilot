import { fmtNumber } from "@/lib/format";

// 확신도 바 (보라 = AI 판단 요소, docs/10 §2). 숫자를 함께 보여 색만으로 전하지 않는다.
export function ConfidenceBar({ value, label = "확신도", small = false }: { value: number | null; label?: string; small?: boolean }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className={`flex items-center gap-2 ${small ? "text-[11px]" : "text-xs"} text-muted`}>
      {label}
      <div
        className="h-[5px] flex-grow overflow-hidden rounded-[3px] bg-bg0"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={value ?? 0}
      >
        <div className="h-[5px] bg-ai" style={{ width: `${pct}%` }} />
      </div>
      <span className="num min-w-[4ch] text-right text-ink">{fmtNumber(value, 2)}</span>
    </div>
  );
}
