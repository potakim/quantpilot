"use client";

// 사이드바 하단 월 손실 한도 카드 (docs/10 §3, ADR 0018 §4). 모든 화면에 고정.
import { cx } from "@/components/ui/primitives";
import { monthLossGauge, worstMonthPnl, type GaugeTone } from "@/lib/gauge";
import { marketLabel } from "@/lib/labels";
import { usePortfolio } from "@/lib/queries";

export const GAUGE_BG: Record<GaugeTone, string> = { ok: "bg-ok", warn: "bg-warn", up: "bg-up" };

export function useMonthLoss() {
  const { data, isLoading } = usePortfolio();
  const worst = worstMonthPnl(data?.month_pnl);
  const gauge = monthLossGauge(worst?.pnl ?? null, data?.month_limit ?? Number.NaN);
  return { gauge, market: worst?.market ?? null, loading: isLoading, hasLimit: data?.month_limit != null };
}

export function GaugeBar({ widthPct, tone, height = 6, label }: { widthPct: number; tone: GaugeTone; height?: number; label: string }) {
  return (
    <div
      className="overflow-hidden rounded-[3px] bg-bg3"
      style={{ height }}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(widthPct)}
    >
      <div className={cx("h-full", GAUGE_BG[tone])} style={{ width: `${widthPct}%` }} />
    </div>
  );
}

export function MonthLossCard() {
  const { gauge, market, loading } = useMonthLoss();
  return (
    <div className="flex flex-col gap-2 rounded-[12px] border border-line bg-bg2 p-3.5">
      <div className="text-xs text-muted">월 손실 한도 (서킷브레이커)</div>
      <div className="flex justify-between text-[13px]">
        <span className="num min-w-[6ch] font-semibold">{loading ? "…" : gauge.value}</span>
        <span className="text-muted">{gauge.limit}</span>
      </div>
      <GaugeBar widthPct={gauge.widthPct} tone={gauge.tone} label={`월 손실 한도 사용률 ${Math.round(gauge.widthPct)}%`} />
      <div className="text-[11px] text-muted">
        한도 도달 시 이번 달 신규 진입이 자동 중단됩니다{market ? ` · 기준 ${marketLabel(market)}` : ""}
      </div>
    </div>
  );
}
