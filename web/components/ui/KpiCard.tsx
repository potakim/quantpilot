import type { ReactNode } from "react";
import { Card, cx } from "@/components/ui/primitives";

// 대시보드 KPI 카드 (Main.dc.html 행 1). 숫자는 모노 + 최소 폭으로 갱신 때 레이아웃이 밀리지 않게.
export function KpiCard({
  label,
  value,
  unit,
  sub,
  valueClass,
  subClass,
  ai = false,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  sub?: ReactNode;
  valueClass?: string;
  subClass?: string;
  ai?: boolean;
}) {
  return (
    <Card as="div" ai={ai} className="flex flex-col gap-1.5 px-5 py-[18px]">
      <div className="flex items-center gap-1.5 text-xs text-muted">{label}</div>
      <div className={cx("num min-w-[10ch] text-[26px] font-bold leading-tight", valueClass)}>
        {value}
        {unit ? <span className="ml-1 font-sans text-sm font-medium text-muted">{unit}</span> : null}
      </div>
      {sub ? <div className={cx("text-xs", subClass ?? "text-muted")}>{sub}</div> : null}
    </Card>
  );
}
