"use client";

// 하단 탭 (Trade.dc.html): 오늘 체결 / 미체결 / 보유 포지션. 게이팅 OFF 섀도 원장(shadow)은 제외한다.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { cx } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { DASH, fmtKrw, fmtPrice, fmtQty, fmtSignedKrw, fmtUsd, kstTime, shortSymbol, toneOf } from "@/lib/format";
import { strategyLabel } from "@/lib/labels";
import type { FillRow, OrderRow, PositionView } from "@/lib/types";

const TONE = { up: "text-up", down: "text-down", muted: "text-muted" } as const;
const OPEN = new Set(["pending", "partial", "queued"]);

type Tab = "fills" | "orders" | "positions";

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th scope="col" className="px-2 py-2 text-left text-[11px] font-normal text-muted first:pl-[18px]">
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cx("px-2 py-[9px] first:pl-[18px]", className)}>{children}</td>;
}

export function TradeTabs({
  market,
  fills,
  orders,
  positions,
}: {
  market: string;
  fills: FillRow[];
  orders: OrderRow[];
  positions: PositionView[];
}) {
  const [tab, setTab] = useState<Tab>("fills");
  const id = useId();
  const qc = useQueryClient();
  const tabsRef = useRef<(HTMLButtonElement | null)[]>([]);
  const todayFills = fills.filter((f) => !f.shadow);
  const openOrders = orders.filter((o) => !o.shadow && OPEN.has(o.status));
  const money = (v: number | null) => (market === "us" ? fmtUsd(v) : fmtKrw(v));
  const cancel = useMutation({
    mutationFn: (orderId: string) => apiFetch(`/orders/${orderId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });
  const tabs: { key: Tab; label: string; n: number }[] = [
    { key: "fills", label: "오늘 체결", n: todayFills.length },
    { key: "orders", label: "미체결", n: openOrders.length },
    { key: "positions", label: "보유 포지션", n: positions.length },
  ];

  function onKey(e: React.KeyboardEvent, i: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
    setTab(tabs[next]!.key);
    tabsRef.current[next]?.focus();
  }

  return (
    <section aria-label="체결·주문·포지션" className="flex flex-col overflow-hidden rounded-card border border-line bg-bg2">
      <div role="tablist" aria-label="거래 내역" className="flex border-b border-line">
        {tabs.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              tabsRef.current[i] = el;
            }}
            id={`${id}-tab-${t.key}`}
            role="tab"
            type="button"
            aria-selected={tab === t.key}
            aria-controls={`${id}-panel`}
            tabIndex={tab === t.key ? 0 : -1}
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => onKey(e, i)}
            className={cx(
              "h-10 border-b-2 px-[18px] text-[13px]",
              tab === t.key ? "border-ai font-semibold text-ink" : "border-transparent text-muted",
            )}
          >
            {t.label} <span className="num">{t.n}</span>
          </button>
        ))}
      </div>
      <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`} className="overflow-x-auto">
        {cancel.isError ? <p role="alert" className="px-[18px] py-2 text-xs text-warn-ink">{reasonText(cancel.error)}</p> : null}
        <table className="w-full min-w-[560px] border-collapse text-xs">
          {tab === "fills" ? (
            <>
              <thead>
                <tr className="border-b border-bg3">
                  <Th>시각</Th>
                  <Th>종목 · 전략</Th>
                  <Th>구분</Th>
                  <Th>체결가</Th>
                  <Th>금액</Th>
                  <Th>AI 판단 · 사유</Th>
                </tr>
              </thead>
              <tbody>
                {todayFills.map((f, i) => (
                  <tr key={f.id} className={cx(i > 0 && "border-t border-bg3")}>
                    <Td className="num text-muted">{kstTime(f.ts)}</Td>
                    <Td>
                      {shortSymbol(f.symbol)} · {strategyLabel(f.strategy)}
                    </Td>
                    <Td className={f.side === "buy" ? "text-up" : "text-down"}>{f.side === "buy" ? "매수" : "매도"}</Td>
                    <Td className="num">{fmtPrice(f.price, f.market)}</Td>
                    <Td className="num">{money(f.price * f.qty)}</Td>
                    <Td className="text-muted">{f.reason || DASH}</Td>
                  </tr>
                ))}
              </tbody>
            </>
          ) : tab === "orders" ? (
            <>
              <thead>
                <tr className="border-b border-bg3">
                  <Th>시각</Th>
                  <Th>종목 · 전략</Th>
                  <Th>구분</Th>
                  <Th>수량</Th>
                  <Th>지정가</Th>
                  <Th>상태</Th>
                </tr>
              </thead>
              <tbody>
                {openOrders.map((o, i) => (
                  <tr key={o.id} className={cx(i > 0 && "border-t border-bg3")}>
                    <Td className="num text-muted">{kstTime(o.ts)}</Td>
                    <Td>
                      {shortSymbol(o.symbol)} · {strategyLabel(o.strategy)}
                    </Td>
                    <Td className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "매수" : "매도"}</Td>
                    <Td className="num">{fmtQty(o.qty, o.market)}</Td>
                    <Td className="num">{o.limit_price == null ? "시장가" : fmtPrice(o.limit_price, o.market)}</Td>
                    <Td>
                      <span className="mr-2 text-muted">{o.status}</span>
                      <button
                        type="button"
                        onClick={() => cancel.mutate(o.id)}
                        disabled={cancel.isPending}
                        className="h-7 rounded-[6px] border border-line px-2 text-[11px] text-ink2"
                      >
                        취소
                      </button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </>
          ) : (
            <>
              <thead>
                <tr className="border-b border-bg3">
                  <Th>종목 · 전략</Th>
                  <Th>수량</Th>
                  <Th>평균 단가</Th>
                  <Th>현재가</Th>
                  <Th>미실현 손익</Th>
                  <Th>손절가</Th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={`${p.symbol}:${p.strategy}`} className={cx(i > 0 && "border-t border-bg3")}>
                    <Td>
                      {shortSymbol(p.symbol)} · {strategyLabel(p.strategy)}
                    </Td>
                    <Td className="num">{fmtQty(p.qty, market)}</Td>
                    <Td className="num">{fmtPrice(p.avg_price, market)}</Td>
                    <Td className="num">{fmtPrice(p.price, market)}</Td>
                    <Td className={cx("num", TONE[toneOf(p.unrealized)])}>
                      {market === "us" ? fmtUsd(p.unrealized) : fmtSignedKrw(p.unrealized)}
                    </Td>
                    <Td className="num text-muted">{fmtPrice(p.stop, market)}</Td>
                  </tr>
                ))}
              </tbody>
            </>
          )}
        </table>
        {(tab === "fills" && !todayFills.length) || (tab === "orders" && !openOrders.length) || (tab === "positions" && !positions.length) ? (
          <p className="px-[18px] py-3 text-xs text-muted">
            {tab === "fills" ? "오늘 체결이 없습니다" : tab === "orders" ? "미체결 주문이 없습니다" : "보유 포지션이 없습니다"}
          </p>
        ) : null}
      </div>
    </section>
  );
}
