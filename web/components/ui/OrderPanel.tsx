"use client";

// 주문 패널 (Trade.dc.html 우측 아래). 수동 주문도 리스크 게이트를 탄다 — API가 RiskManager 사전 검사 후 큐에 넣는다
// (불변식 #9, ADR 0017). 거부(422 RISK_REJECTED)·할트(409 HALTED)면 버튼 아래 주황 블록에 사유를 보인다.
// 비중 버튼의 "AI 권장"은 판단 게이트(half=50%, full=100%)를 보여 줄 뿐 수량을 AI에게 묻지 않는다 (불변식 #7).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { cx } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { DASH, fmtKrw, fmtPrice, fmtQty, fmtUsd, shortSymbol } from "@/lib/format";

type Side = "buy" | "sell";
type OType = "market" | "limit";

export interface OrderPanelProps {
  market: string;
  symbol: string;
  paper: boolean | undefined;
  price: number | null;
  cash: number | null;
  positionQty: number;
  aiGate: string | null;
}

function parseAmount(s: string): number {
  const v = Number(s.replace(/[^0-9.]/g, ""));
  return Number.isFinite(v) ? v : 0;
}

export function OrderPanel({ market, symbol, paper, price, cash, positionQty, aiGate }: OrderPanelProps) {
  const [side, setSide] = useState<Side>("buy");
  const [type, setType] = useState<OType>("market");
  const [amountText, setAmountText] = useState("");
  const [limitText, setLimitText] = useState("");
  const qc = useQueryClient();
  const id = useId();
  const usd = market === "us";
  const money = (v: number | null) => (usd ? fmtUsd(v) : fmtKrw(v));
  const amount = parseAmount(amountText);
  const limitPrice = type === "limit" ? parseAmount(limitText) : null;
  const ref = type === "limit" ? limitPrice : price;
  const qty = ref && amount ? amount / ref : null;
  const base = side === "buy" ? cash : price != null ? positionQty * price : null;
  const aiPct = aiGate === "full" ? 100 : aiGate === "half" ? 50 : null;

  const order = useMutation({
    mutationFn: () =>
      apiFetch<{ order: { id: string; qty: number; status: string } }>("/orders", {
        method: "POST",
        json: {
          market,
          symbol,
          side,
          amount,
          type,
          ...(type === "limit" ? { limit_price: limitPrice } : {}),
        },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  function setPct(pct: number) {
    if (base == null) return;
    const v = (base * pct) / 100;
    setAmountText(usd ? v.toFixed(2) : String(Math.floor(v)));
  }

  const canSubmit = paper !== undefined && amount > 0 && (type === "market" || (limitPrice ?? 0) > 0) && !order.isPending;
  const sideText = side === "buy" ? "매수" : "매도";

  return (
    <section aria-label="주문" className="flex flex-grow flex-col gap-3 rounded-card border border-line bg-bg2 p-4">
      <div role="group" aria-label="매수·매도" className="flex gap-1.5">
        {(["buy", "sell"] as const).map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={side === s}
            onClick={() => setSide(s)}
            className={cx(
              "h-9 flex-grow rounded-btn text-[13px]",
              side === s
                ? s === "buy"
                  ? "bg-up-bg font-bold text-up"
                  : "bg-down-bg font-bold text-down"
                : "border border-line bg-transparent font-semibold text-muted",
            )}
          >
            {s === "buy" ? "매수" : "매도"}
          </button>
        ))}
      </div>
      <div role="group" aria-label="주문 방식" className="flex gap-1.5 text-xs">
        {(
          [
            ["market", "시장가"],
            ["limit", "지정가"],
          ] as const
        ).map(([t, label]) => (
          <button
            key={t}
            type="button"
            aria-pressed={type === t}
            onClick={() => setType(t)}
            className={cx(
              "h-[30px] flex-grow rounded-[7px] border border-line",
              type === t ? "bg-bg3 font-semibold text-ink" : "bg-transparent text-muted",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {type === "limit" ? (
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          지정가 ({usd ? "USD" : "KRW"})
          <input
            inputMode="decimal"
            autoComplete="off"
            value={limitText}
            onChange={(e) => setLimitText(e.target.value)}
            placeholder={price != null ? fmtPrice(price, market) : ""}
            className="num h-10 rounded-btn border border-line bg-bg0 px-3 text-[15px] text-ink"
          />
        </label>
      ) : null}
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        주문 금액 ({usd ? "USD" : "KRW"})
        <input
          inputMode="decimal"
          autoComplete="off"
          value={amountText}
          onChange={(e) => setAmountText(e.target.value)}
          aria-describedby={`${id}-est`}
          className="num h-10 rounded-btn border border-line bg-bg0 px-3 text-[15px] text-ink"
        />
      </label>
      <div role="group" aria-label={side === "buy" ? "가용 현금 대비 비중" : "보유 평가액 대비 비중"} className="flex gap-1.5">
        {[10, 25].map((p) => (
          <button
            key={p}
            type="button"
            disabled={base == null}
            onClick={() => setPct(p)}
            className="h-7 flex-grow rounded-[6px] border border-line bg-transparent text-[11px] text-muted disabled:opacity-50"
          >
            {p}%
          </button>
        ))}
        <button
          type="button"
          disabled={base == null || aiPct == null}
          onClick={() => aiPct != null && setPct(aiPct)}
          title={aiPct == null ? "최근 판단이 보류이거나 없어 권장 비중이 없습니다" : "최근 판단의 게이팅 결과"}
          className="h-7 flex-grow rounded-[6px] border border-ai bg-ai-bg text-[11px] font-semibold text-ai-ink disabled:opacity-50"
        >
          {aiPct == null ? "AI 권장 없음" : `AI 권장 ${aiPct}%`}
        </button>
        <button
          type="button"
          disabled={base == null}
          onClick={() => setPct(100)}
          className="h-7 flex-grow rounded-[6px] border border-line bg-transparent text-[11px] text-muted disabled:opacity-50"
        >
          100%
        </button>
      </div>
      <dl id={`${id}-est`} className="flex flex-col gap-1 text-xs text-muted">
        <div className="flex justify-between">
          <dt>예상 수량</dt>
          <dd className="num text-ink">{qty == null ? DASH : `${fmtQty(qty, market)} ${shortSymbol(symbol)}`}</dd>
        </div>
        <div className="flex justify-between">
          <dt>기준가 ({type === "limit" ? "지정가" : "현재가"})</dt>
          <dd className="num text-ink">{fmtPrice(ref, market)}</dd>
        </div>
        <div className="flex justify-between">
          <dt>{side === "buy" ? "가용 현금" : "보유 수량"}</dt>
          <dd className="num text-ink">{side === "buy" ? money(cash) : fmtQty(positionQty, market)}</dd>
        </div>
      </dl>
      <button
        type="button"
        disabled={!canSubmit}
        onClick={() => order.mutate()}
        className={cx(
          "h-[46px] rounded-block text-sm font-bold text-bg0 disabled:opacity-60",
          side === "buy" ? "bg-up" : "bg-down",
        )}
      >
        {order.isPending ? "리스크 게이트 확인 중…" : `${paper === undefined ? "" : paper ? "페이퍼 " : "실전 "}${sideText} · ${amount ? money(amount) : DASH}`}
      </button>
      {order.isError ? (
        <p role="alert" className="rounded-block border border-warn-line bg-warn-bg px-3 py-2.5 text-xs text-warn-ink">
          {reasonText(order.error)}
        </p>
      ) : null}
      {order.isSuccess ? (
        <p role="status" className="rounded-block border border-ok-bg bg-ok-bg px-3 py-2.5 text-xs text-ok-ink">
          주문 접수 · 엔진 대기열 (수량 {fmtQty(order.data.order.qty, market)}). 체결은 오늘 체결 탭에서 확인하세요.
        </p>
      ) : null}
      <p className="text-center text-[11px] text-muted">수동 주문도 리스크 게이트를 통과해야 체결됩니다</p>
    </section>
  );
}
