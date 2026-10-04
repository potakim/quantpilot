"use client";

// 거래 · 차트 (Trade.dc.html): 3열 230px / 가변 / 310px. WS ticks·orderbook·fills로 실시간 갱신.
import { useMemo, useState } from "react";
import { AiPanel } from "@/components/trade/AiPanel";
import { PriceChart, type EntryMark } from "@/components/trade/PriceChart";
import { TradeTabs } from "@/components/trade/TradeTabs";
import {
  OrderbookCard,
  Watchlist,
  displaySymbol,
  strategyState,
  useLivePrice,
  usePrevClose,
} from "@/components/trade/Watchlist";
import { IconLock } from "@/components/ui/Icons";
import { OrderPanel } from "@/components/ui/OrderPanel";
import { Card, CardSkeleton, ErrorNote, Segmented, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { TF_SECONDS, toBars } from "@/lib/chart-data";
import { DASH, fmtNumber, fmtPct, fmtPrice, kstDayStartIso, toneOf } from "@/lib/format";
import { marketLabel } from "@/lib/labels";
import { useLiveChannels } from "@/lib/live";
import {
  useCandles,
  useFills,
  useHealth,
  useJudgments,
  useLiveMarkets,
  useOrders,
  usePortfolio,
  usePositions,
  useQuote,
  useSettings,
  useStrategies,
} from "@/lib/queries";
import type { Orderbook } from "@/lib/types";
import { useUi } from "@/lib/ui-store";
import { useLive } from "@/lib/ws";

const TFS = [
  { value: "1m", label: "1분" },
  { value: "5m", label: "5분" },
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "1d", label: "일" },
] as const;
type Tf = (typeof TFS)[number]["value"];

const TONE = { up: "text-up", down: "text-down", muted: "text-ink" } as const;

export function TradeView({ market, symbol }: { market: string; symbol: string }) {
  const [tf, setTf] = useState<Tf>("5m");
  const [today] = useState(() => kstDayStartIso());
  const strategies = useStrategies();
  const health = useHealth();
  const live = useLiveMarkets();
  const isLive = live.has(market); // 엔진 없는 시장은 시세·주문이 없다 (ADR 0031)
  const tab = useUi((s) => s.market);
  const portfolio = usePortfolio();
  const settings = useSettings();
  const quote = useQuote(market, symbol, isLive);
  const candles = useCandles(market, symbol, tf, 300, isLive);
  const fills = useFills({ market, from: today, limit: 200 });
  const orders = useOrders({ market, limit: 100 });
  const positions = usePositions(market);
  const judgment = useJudgments({ symbol, limit: 1 });
  const liveBook = useLive((s) => s.orderbooks[`${market}:${symbol}`]) as Orderbook | undefined;
  const { price, ts } = useLivePrice(market, symbol, quote.data?.price);
  const prev = usePrevClose(market, symbol, isLive);

  const watchSymbols = useMemo(
    () =>
      (strategies.data ?? [])
        .filter((s) => live.has(s.market))
        .flatMap((s) => s.symbols.map((sym) => `ticks:${s.market}:${sym}`)),
    [strategies.data, live],
  );
  useLiveChannels([`ticks:${market}:${symbol}`, `orderbook:${market}:${symbol}`, ...watchSymbols]);

  const bars = useMemo(() => toBars(candles.data ?? []), [candles.data]);
  const fillRows = useMemo(() => fills.data?.items ?? [], [fills.data]);
  const entries: EntryMark[] = useMemo(
    () =>
      fillRows
        .filter((f) => !f.shadow && f.symbol === symbol && f.side === "buy")
        .map((f) => ({ time: Math.floor(Date.parse(f.ts) / 1000), ts: f.ts })),
    [fillRows, symbol],
  );
  const { target, maScore } = strategyState(quote.data?.strategy);
  const change = price != null && prev ? price / prev - 1 : null;
  const latest = judgment.data?.items[0] ?? null;
  const posList = positions.data ?? [];
  const positionQty = posList.filter((p) => p.symbol === symbol).reduce((a, p) => a + p.qty, 0);
  const held = new Set(posList.map((p) => `${p.market ?? market}:${p.symbol}`));
  const name = displaySymbol(market, symbol);
  const tfLabel = TFS.find((t) => t.value === tf)!.label;

  return (
    <div className="flex flex-col gap-4 p-4 lg:flex-row">
      <h1 className="sr-only">거래 · 차트 — {name}</h1>
      <div className="flex w-full shrink-0 flex-col gap-4 lg:w-[230px]">
        {strategies.isLoading ? (
          <CardSkeleton lines={5} />
        ) : (
          <Watchlist
            strategies={strategies.data ?? []}
            market={market}
            symbol={symbol}
            heldSymbols={held}
            tab={tab}
            live={live}
          />
        )}
        {isLive ? (
          <OrderbookCard market={market} symbol={symbol} book={liveBook ?? quote.data?.orderbook ?? null} price={price} />
        ) : null}
      </div>

      <div className="flex min-w-0 flex-grow flex-col gap-4">
        <section aria-label={`${name} 차트`} className="flex flex-col overflow-hidden rounded-card border border-line bg-bg2">
          <div className="flex flex-wrap items-center gap-4 border-b border-line px-[18px] py-3.5">
            <div className="flex items-baseline gap-2.5">
              <span className="text-lg font-bold">{name}</span>
              {isLive ? (
                <>
                  <span className={cx("num min-w-[11ch] text-xl font-bold", TONE[toneOf(change)])}>
                    {fmtPrice(price, market)}
                  </span>
                  <span className={cx("num text-[13px]", TONE[toneOf(change)])}>
                    {change == null || price == null || prev == null
                      ? DASH
                      : `${fmtPct(change)} (${price - prev > 0 ? "+" : ""}${fmtPrice(price - prev, market)})`}
                  </span>
                </>
              ) : (
                <span className="text-xs text-muted">{marketLabel(market)}</span>
              )}
            </div>
            {target != null ? (
              <div className="flex items-center gap-1.5 rounded-pill bg-ai-bg px-2.5 py-1 text-xs font-semibold text-ai-ink">
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ai" />
                변동성 돌파 목표가 {fmtPrice(target, market)} · {price != null && price >= target ? "돌파" : "미돌파"}
              </div>
            ) : null}
            {maScore != null ? <div className="text-xs text-muted">이평 스코어 {fmtNumber(maScore, 2)}</div> : null}
            <div className="flex-grow" />
            {isLive ? <Segmented label="봉 간격" size="sm" value={tf} onChange={setTf} options={[...TFS]} /> : null}
          </div>
          {!isLive ? (
            <div className="p-4">
              <PendingMarketNote market={market} />
            </div>
          ) : candles.isError ? (
            <div className="p-4">
              <ErrorNote>캔들을 불러오지 못했습니다: {reasonText(candles.error)}</ErrorNote>
            </div>
          ) : candles.isLoading ? (
            <div className="skeleton m-4 h-[328px]" aria-hidden="true" />
          ) : bars.length ? (
            <PriceChart
              bars={bars}
              tfSeconds={TF_SECONDS[tf]!}
              target={target}
              entries={entries}
              livePrice={price}
              liveTs={ts}
              label={`${name} ${tfLabel}봉 캔들 차트${target != null ? ", 변동성 돌파 목표가" : ""}와 20개 이동평균선`}
            />
          ) : (
            <p className="p-4 text-xs text-muted">이 구간의 캔들이 없습니다</p>
          )}
          {quote.isError ? <p className="px-[18px] pb-3 text-xs text-muted">현재가: {reasonText(quote.error)}</p> : null}
        </section>
        <TradeTabs market={market} fills={fillRows} orders={orders.data?.items ?? []} positions={posList} />
      </div>

      <div className="flex w-full shrink-0 flex-col gap-4 lg:w-[310px]">
        <AiPanel symbol={symbol} judgment={latest} rules={settings.data?.risk_rules ?? null} />
        {isLive ? (
          <OrderPanel
            market={market}
            symbol={symbol}
            paper={health.data?.paper}
            price={price}
            cash={portfolio.data?.by_market[market]?.cash ?? null}
            positionQty={positionQty}
            aiGate={latest?.gate ?? null}
            feeRate={quote.data?.fee_rate ?? null}
          />
        ) : (
          <Card as="section" aria-label="주문" className="flex flex-col gap-2 p-4">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-muted">
              <IconLock size={14} /> 주문 잠김
            </div>
            <p className="text-xs leading-relaxed text-muted">
              {marketLabel(market)} 브로커는 2단계에서 연결됩니다. 지금은 이 종목을 주문할 수 없습니다.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}

/** 실시간 엔진이 없는 시장 안내 (ADR 0031). 시세·호가·주문이 없고, 전략은 백테스트로 본다. */
function PendingMarketNote({ market }: { market: string }) {
  return (
    <div role="status" className="flex flex-col gap-1.5 rounded-block border border-warn-line bg-warn-bg px-4 py-3 text-xs">
      <span className="font-semibold text-warn-ink">{marketLabel(market)}은 2단계에서 실시간 운영됩니다</span>
      <span className="leading-relaxed text-ink2">
        이 시장의 시세 수신·주문 엔진은 아직 연결되지 않았습니다. 전략 성과는 백테스트 화면에서 확인할 수 있습니다.
      </span>
    </div>
  );
}
