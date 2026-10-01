"use client";

// 할트 배너 (docs/10 §4.1): 활성 전략 표 위에 주황 "신규 진입 중단: {사유}" + "브로커 기준으로 맞추기".
// 할트를 푸는 API는 POST /reconcile/{market}/accept-broker 하나뿐이고 비밀번호 재확인이 필요하다 (ADR 0015·0017).
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { apiFetch } from "@/lib/api";
import { marketLabel } from "@/lib/labels";
import { usePortfolio } from "@/lib/queries";

export function HaltBanner() {
  const { data } = usePortfolio();
  const qc = useQueryClient();
  const [target, setTarget] = useState<string | null>(null);
  const halted = Object.entries(data?.halted ?? {}).filter((e): e is [string, string] => Boolean(e[1]));
  if (!halted.length) return null;

  async function accept(password: string) {
    if (!target) return;
    await apiFetch(`/reconcile/${target}/accept-broker`, { method: "POST", json: { confirm_password: password } });
    for (const key of ["portfolio", "health", "positions", "risk-events"]) await qc.invalidateQueries({ queryKey: [key] });
  }

  return (
    <>
      {halted.map(([market, reason]) => (
        <div
          key={market}
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-block border border-warn-line bg-warn-bg px-4 py-3 text-[13px] text-warn-ink"
        >
          <span>
            <strong className="font-semibold">신규 진입 중단: {reason}</strong>
            <span className="text-warn-ink"> · {marketLabel(market)} (청산 주문은 계속 허용)</span>
          </span>
          <button
            type="button"
            onClick={() => setTarget(market)}
            className="h-9 min-w-[44px] rounded-btn border border-warn-line bg-bg2 px-3.5 text-[13px] font-semibold text-warn-ink"
          >
            브로커 기준으로 맞추기
          </button>
        </div>
      ))}
      <ConfirmDialog
        open={target !== null}
        title="브로커 기준으로 맞추기"
        description={
          <>
            {marketLabel(target)}의 DB 포지션을 브로커 계좌 기준으로 덮어쓰고, 정합 이벤트를 해결한 뒤 할트를 풉니다. 되돌릴 수
            없습니다. 계속하려면 비밀번호를 다시 입력하세요.
          </>
        }
        confirmLabel="맞추기"
        onConfirm={accept}
        onClose={() => setTarget(null)}
      />
    </>
  );
}
