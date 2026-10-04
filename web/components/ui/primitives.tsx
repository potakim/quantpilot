// 공통 UI 조각: Card, Badge, Skeleton, Segmented, ErrorNote. 아트보드 마크업을 그대로 옮겼다 (docs/10 §2).
import type { ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  children,
  className,
  ai = false,
  line,
  as: Tag = "section",
  ...rest
}: {
  children: ReactNode;
  className?: string;
  ai?: boolean;
  /** 테두리 색 클래스 (기본 border-line, ai면 border-ai-line). 같이 붙이면 어느 쪽이 이길지 모르므로 바꿔 끼운다 */
  line?: string;
  as?: "section" | "div" | "aside" | "article";
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={cx("rounded-card border bg-bg2", line ?? (ai ? "border-ai-line" : "border-line"), className)}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export type BadgeTone = "ok" | "warn" | "warn2" | "ai" | "up" | "muted";

const BADGE: Record<BadgeTone, string> = {
  ok: "bg-ok-bg text-ok-ink",
  warn: "bg-warn-bg text-warn-ink",
  warn2: "bg-warn-bg-2 text-warn-ink",
  ai: "bg-ai-bg text-ai-ink",
  up: "bg-up-bg text-up",
  muted: "bg-bg3 text-muted",
};

export function Badge({
  tone,
  children,
  className,
}: {
  tone: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cx("inline-flex items-center rounded-pill px-2 py-0.5 text-[11px] font-semibold", BADGE[tone], className)}>
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cx("skeleton block", className)} />;
}

/** 카드 하나 분량의 골격 (로딩 중). */
export function CardSkeleton({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-3 rounded-card border border-line bg-bg2 p-5", className)} aria-busy="true">
      <span className="sr-only">불러오는 중</span>
      <Skeleton className="h-3 w-24" />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cx("h-4", i % 2 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export interface SegOption<T extends string> {
  value: T;
  label: string;
}

/** 아트보드의 탭형 버튼 묶음. 선택 상태는 aria-pressed로 알린다. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  framed = true,
}: {
  options: SegOption<T>[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
  framed?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cx("flex", framed ? "gap-0.5 rounded-btn border border-line bg-bg0 p-[3px]" : "gap-1")}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={cx(
              "rounded-[6px] px-2.5 text-xs",
              size === "sm" ? "h-[26px]" : "h-7",
              !framed && "border border-line",
              on ? "bg-bg3 font-semibold text-ink" : "bg-transparent text-muted",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** 데이터가 없거나 API가 아직 주지 않는 값 — 지어내지 않고 이유를 적는다. */
export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="rounded-block bg-bg3 px-3 py-2.5 text-xs text-muted">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-block border border-warn-line bg-warn-bg px-3 py-2.5 text-xs text-warn-ink">
      {children}
    </p>
  );
}
