"use client";

// 입력 조각: 슬라이더·버튼·칩·라디오 카드·체크 줄. 전략 설정 아트보드(Strategy.dc.html) 마크업을 옮겼다.
// 강조색(보라)은 아트보드가 쓰는 자리만: 슬라이더·선택 칩·선택된 라디오·저장 버튼 (docs/10 §2).
import type { ReactNode } from "react";
import { cx } from "@/components/ui/primitives";

export type ButtonVariant = "outline" | "ghost" | "primary";

const BUTTON: Record<ButtonVariant, string> = {
  outline: "h-[34px] rounded-btn border border-line bg-transparent px-3.5 text-xs font-semibold text-ink hover:text-ink",
  ghost: "h-[34px] rounded-btn border border-line bg-transparent px-3.5 text-xs text-muted hover:text-ink2",
  primary: "h-10 rounded-block bg-ai px-4 text-[13px] font-bold text-bg0 hover:text-bg0",
};

/** 버튼·링크에 같이 쓰는 클래스 (링크는 next/link에 붙인다). */
export function buttonClass(variant: ButtonVariant, className?: string): string {
  return cx(
    "inline-flex items-center justify-center gap-1.5 no-underline disabled:cursor-not-allowed disabled:opacity-60",
    BUTTON[variant],
    className,
  );
}

export function Button({
  variant = "outline",
  className,
  children,
  ...rest
}: { variant?: ButtonVariant; children: ReactNode } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={buttonClass(variant, className)} {...rest}>
      {children}
    </button>
  );
}

/** 라벨 · 슬라이더 · 값. 끌어서 놓을 때(또는 키를 뗄 때) onCommit — 매 움직임마다 저장하지 않는다. */
export function Slider({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
  onCommit,
  disabled = false,
  labelWidth = "w-[92px]",
  valueWidth = "w-[52px]",
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (v: number) => void;
  onCommit?: (v: number) => void;
  disabled?: boolean;
  labelWidth?: string;
  valueWidth?: string;
}) {
  const commit = (e: React.SyntheticEvent<HTMLInputElement>) => onCommit?.(Number(e.currentTarget.value));
  return (
    <label className={cx("flex items-center gap-2.5 text-xs", disabled && "opacity-60")}>
      <span className={cx("shrink-0 text-muted", labelWidth)}>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-valuetext={display}
        onChange={(e) => onChange(Number(e.currentTarget.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        className="min-w-0 flex-grow accent-ai disabled:cursor-not-allowed"
      />
      <span className={cx("num shrink-0 text-right", valueWidth)}>{display}</span>
    </label>
  );
}

/** 선택된 값 칩 (보라) — 룩백·이평 윈도우처럼 고른 값을 보여 준다. */
export function Chip({ children }: { children: ReactNode }) {
  return <span className="rounded-pill bg-ai-bg px-[9px] py-[3px] font-semibold text-ai-ink">{children}</span>;
}

/** 종목 칩 (모노, 회색). */
export function SymbolChip({ children }: { children: ReactNode }) {
  return <span className="num rounded-[6px] bg-bg3 px-2 py-[3px]">{children}</span>;
}

/** 켜고 끄는 칩: 켜짐 = 보라 칩 "사용", 꺼짐 = 점선 "사용 안 함" (아트보드의 "노이즈 K"). label은 읽기용 이름. */
export function FlagChip({
  on,
  label,
  onChange,
  disabled = false,
}: {
  on: boolean;
  label: string;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={cx(
        "rounded-pill px-[9px] py-[3px] text-xs disabled:cursor-not-allowed disabled:opacity-60",
        on ? "bg-ai-bg font-semibold text-ai-ink" : "border border-dashed border-muted2 text-muted",
      )}
    >
      {on ? "사용" : "사용 안 함"}
    </button>
  );
}

/** 라디오 카드: 선택 = 보라 테두리·배경. */
export function ChoiceCard({
  name,
  value,
  checked,
  onChange,
  title,
  meta,
  disabled = false,
}: {
  name: string;
  value: string;
  checked: boolean;
  onChange: (v: string) => void;
  title: ReactNode;
  meta?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label
      className={cx(
        "flex items-center gap-2.5 rounded-[8px] border px-2.5 py-2 text-xs",
        checked ? "border-ai bg-ai-bg" : "border-line bg-bg3",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onChange(value)}
        className="accent-ai"
      />
      <span className="flex-grow">{title}</span>
      {meta != null ? <span className={cx("text-right", checked ? "text-ai-ink" : "text-muted")}>{meta}</span> : null}
    </label>
  );
}

/** 체크 줄: 체크박스 · 이름 · 오른쪽 설명. */
export function CheckRow({
  checked,
  onChange,
  title,
  meta,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  title: ReactNode;
  meta?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label
      className={cx(
        "flex items-center gap-2.5 rounded-[8px] bg-bg3 px-2.5 py-2 text-xs",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.currentTarget.checked)}
        className="accent-ai"
      />
      <span className="flex-grow">{title}</span>
      {meta != null ? <span className="text-right text-muted">{meta}</span> : null}
    </label>
  );
}
