"use client";

// ON/OFF 토글 (docs/10 §5: role="switch" aria-checked). 모바일은 44×26 (터치 타깃).
import { cx } from "@/components/ui/primitives";

export function Toggle({
  checked,
  onChange,
  label,
  size = "sm",
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  size?: "sm" | "lg";
  disabled?: boolean;
}) {
  const lg = size === "lg";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative shrink-0 rounded-pill p-0 disabled:opacity-60",
        lg ? "h-[26px] w-[44px]" : "h-[22px] w-[40px]",
        checked ? "bg-ok" : "bg-bg4",
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          "absolute top-[3px] rounded-full",
          lg ? "h-5 w-5" : "h-4 w-4",
          checked ? "right-[3px] bg-bg0" : "left-[3px] bg-muted",
        )}
      />
    </button>
  );
}
