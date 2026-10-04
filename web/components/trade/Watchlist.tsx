"use client";

// 관심 종목 (Trade.dc.html 좌측): 전략 대상 종목 + 현재가·전일 대비·전략 상태 부제. 호가 카드도 여기.
import Link from "next/link";
import { cx } from "@/components/ui/primitives";
import { DASH, fmtPct, fmtPrice, shortSymbol, toneOf } from "@/lib/format";
import { marketLabel, strategyLabel } from "@/lib/labels";
import { useCandles, useQuote } from "@/lib/queries";
import type { Orderbook, StrategyView } from "@/lib/types";
import { useLive } from "@/lib/ws";

const TONE = { up: "text-up", down: "text-down", muted: "text-muted" } as const;

export function displaySymbol(market: string, symbol: string): string {
  return market === "upbit" && symbol.startsWith("KRW-") ? `${shortSymbol(symbol)}/KRW` : symbol;
}

/** 전략 상태(허브 st:{m}:{s})에서 목표가·이평 스코어를 꺼낸다. 키가 없으면 null. */
export function strategyState(st: Record<string, unknown> | null | undefined) {
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return { target: n(st?.target) ?? n(st?.target_price), maScore: n(st?.ma_score) };
}

export function useLivePrice(market: string, symbol: string, fallback: number | null | undefined) {
  const tick = useLive((s) => s.ticks[`${market}:${symbol}`]);
  return { price: tick?.price ?? fallback ?? null, ts: tick?.ts ?? null };
}

/** 전일 종가 (일봉 2개 중 앞의 것). enabled=false면 요청하지 않는다. */
export function usePrevClose(market: string, symbol: string, enabled = true): number | null {
  const d = useCandles(market, symbol, "1d", 2, enabled);
  const rows = d.data ?? [];
  return rows.length >= 2 ? rows[rows.length - 2]!.c : null;
}

function WatchRow({
  market,
  symbol,
  strategy,
  active,
  held,
}: {
  market: string;
  symbol: string;
  strategy: string;
  active: boolean;
  held: boolean;
}) {
  const quote = useQuote(market, symbol);
  const { price } = useLivePrice(market, symbol, quote.data?.price);
  const prev = usePrevClose(market, symbol);
  const change = price != null && prev ? price / prev - 1 : null;
  const { target } = strategyState(quote.data?.strategy);
  let sub: { text: string; cls: string };
  if (held) sub = { text: "보유 중", cls: "text-ok" };
  else if (target && price != null) sub = { text: `목표가 대비 ${fmtPct(price / target - 1)}`, cls: "text-muted" };
  else if (quote.isError) sub = { text: "시세 없음", cls: "text-muted" };
  else sub = { text: strategyLabel(strategy), cls: "text-muted" };
  return (
    <li>
      <Link
        href={`/trade/${market}/${encodeURIComponent(symbol)}`}
        aria-current={active ? "page" : undefined}
        className={cx(
          "flex items-center justify-between border-b border-bg3 px-3.5 py-2.5 text-ink no-underline hover:bg-bg3 hover:text-ink",
          active && "bg-bg3",
        )}
      >
        <span className="flex flex-col gap-px">
          <span className="flex items-center gap-1.5 text-[13px] font-semibold">
            {displaySymbol(market, symbol)}
            {held ? <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ok" /> : null}
          </span>
          <span className={cx("text-[11px]", sub.cls)}>{sub.text}</span>
        </span>
        <span className="num flex flex-col items-end gap-px">
          <span className="min-w-[9ch] text-right text-[13px]">{fmtPrice(price, market)}</span>
          <span className={cx("text-[11px]", TONE[toneOf(change)])}>{change == null ? DASH : fmtPct(change)}</span>
        </span>
      </Link>
    </li>
  );
}

/** 실시간 엔진이 없는 시장의 행: 시세를 요청하지 않고 "2단계 예정"으로 보인다 (ADR 0031). */
function PendingRow({ market, symbol, strategy, active }: { market: string; symbol: string; strategy: string; active: boolean }) {
  return (
    <li>
      <Link
        href={`/trade/${market}/${encodeURIComponent(symbol)}`}
        aria-current={active ? "page" : undefined}
        className={cx(
          "flex items-center justify-between border-b border-bg3 px-3.5 py-2.5 text-ink no-underline hover:bg-bg3 hover:text-ink",
          active && "bg-bg3",
        )}
      >
        <span className="flex flex-col gap-px">
          <span className="text-[13px] font-semibold">{displaySymbol(market, symbol)}</span>
          <span className="text-[11px] text-muted">
            {strategyLabel(strategy)} · {marketLabel(market)} 2단계 예정
          </span>
        </span>
        <span className="num text-[13px] text-muted">{DASH}</span>
      </Link>
    </li>
  );
}

/** 관심 종목 목록: 전략 대상 종목을 시장 탭으로 거르고, 실시간 시장을 앞에 둔다. */
export function watchItems(
  strategies: StrategyView[],
  tab: string,
  live: Set<string>,
): { market: string; symbol: string; strategy: string }[] {
  const seen = new Set<string>();
  return strategies
    .flatMap((s) => s.symbols.map((sym) => ({ market: s.market, symbol: sym, strategy: s.name })))
    .filter((i) => {
      const k = `${i.market}:${i.symbol}`;
      if (seen.has(k) || (tab !== "all" && i.market !== tab)) return false;
      seen.add(k);
      return true;
    })
    .sort((a, b) => Number(live.has(b.market)) - Number(live.has(a.market)));
}

export function Watchlist({
  strategies,
  market,
  symbol,
  heldSymbols,
  tab,
  live,
}: {
  strategies: StrategyView[];
  market: string;
  symbol: string;
  heldSymbols: Set<string>;
  tab: string;
  live: Set<string>;
}) {
  const unique = watchItems(strategies, tab, live);
  return (
    <section aria-labelledby="watch-title" className="flex flex-col overflow-hidden rounded-card border border-line bg-bg2">
      <div className="flex items-center justify-between border-b border-line px-3.5 py-3">
        <h2 id="watch-title" className="text-[13px] font-semibold">
          관심 종목
        </h2>
        <span className="text-[11px] text-muted">전략 대상 {unique.length}</span>
      </div>
      <ul className="max-h-[420px] overflow-y-auto">
        {unique.map((i) =>
          live.has(i.market) ? (
            <WatchRow
              key={`${i.market}:${i.symbol}`}
              market={i.market}
              symbol={i.symbol}
              strategy={i.strategy}
              active={i.market === market && i.symbol === symbol}
              held={heldSymbols.has(`${i.market}:${i.symbol}`)}
            />
          ) : (
            <PendingRow
              key={`${i.market}:${i.symbol}`}
              market={i.market}
              symbol={i.symbol}
              strategy={i.strategy}
              active={i.market === market && i.symbol === symbol}
            />
          ),
        )}
        {!unique.length ? <li className="px-3.5 py-3 text-xs text-muted">이 시장의 전략 대상 종목이 없습니다</li> : null}
      </ul>
    </section>
  );
}

export function OrderbookCard({
  market,
  symbol,
  book,
  price,
}: {
  market: string;
  symbol: string;
  book: Orderbook | null;
  price: number | null;
}) {
  const asks = [...(book?.asks ?? [])].sort((a, b) => a[0] - b[0]).slice(0, 5).reverse();
  const bids = [...(book?.bids ?? [])].sort((a, b) => b[0] - a[0]).slice(0, 5);
  const maxQ = Math.max(1e-12, ...asks.map((a) => a[1]), ...bids.map((b) => b[1]));
  const bestAsk = asks[asks.length - 1]?.[0];
  const bestBid = bids[0]?.[0];
  const spread = bestAsk && bestBid ? (bestAsk - bestBid) / ((bestAsk + bestBid) / 2) : null;
  const Row = ({ p, q, side }: { p: number; q: number; side: "ask" | "bid" }) => (
    <li className="relative flex justify-between px-3.5 py-1.5">
      <span
        aria-hidden="true"
        className={cx("absolute inset-y-0 right-0", side === "ask" ? "bg-down-bg" : "bg-up-bg")}
        style={{ width: `${(q / maxQ) * 100}%` }}
      />
      <span className={cx("relative", side === "ask" ? "text-down" : "text-up")}>{fmtPrice(p, market)}</span>
      <span className="relative text-muted">{q.toFixed(2)}</span>
    </li>
  );
  return (
    <section aria-labelledby="ob-title" className="flex flex-grow flex-col overflow-hidden rounded-card border border-line bg-bg2">
      <h2 id="ob-title" className="border-b border-line px-3.5 py-3 text-[13px] font-semibold">
        호가 · {displaySymbol(market, symbol)}
      </h2>
      {book && (asks.length || bids.length) ? (
        <ul className="num flex flex-col text-xs" aria-label="매도·매수 호가">
          {asks.map(([p, q]) => (
            <Row key={`a${p}`} p={p} q={q} side="ask" />
          ))}
          <li className="flex justify-between border-y border-line bg-bg3 px-3.5 py-2">
            <span className="font-semibold text-ink">{fmtPrice(price, market)}</span>
            <span className="text-muted">스프레드 {spread == null ? DASH : fmtPct(spread, { signed: false, digits: 2 })}</span>
          </li>
          {bids.map(([p, q]) => (
            <Row key={`b${p}`} p={p} q={q} side="bid" />
          ))}
        </ul>
      ) : (
        <p className="px-3.5 py-3 text-xs text-muted">호가 정보가 없습니다 (엔진 시세 수신 대기)</p>
      )}
    </section>
  );
}
