"use client";

// 포트폴리오 (원본 아트보드 없음 — 대시보드·전략 설정과 같은 카드·토큰으로 만든 새 화면, docs/10 §4.7).
// 요약 카드 4개 → 시장별 계좌 카드(엔진 없는 시장은 "2단계 예정", ADR 0031) → 자산 곡선 + 전략별 배정 대비 투입
// (ADR 0032) → 보유 종목 표(카드 안 시장 선택으로 거름).
import Link from "next/link";
import { useState } from "react";
import { GaugeBar } from "@/components/layout/MonthLossCard";
import { EquityCurveCard } from "@/components/ui/EquityCurveCard";
import { KpiCard } from "@/components/ui/KpiCard";
import { Badge, Card, CardSkeleton, EmptyNote, ErrorNote, Segmented, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { DASH, fmtKrw, fmtPct } from "@/lib/format";
import { cashKrw, todayPnlView } from "@/lib/metrics";
import { holdingRows, marketCards, strategyUsage, type MarketCard } from "@/lib/portfolio";
import { useLiveMarkets, usePortfolio, usePositions, useStrategies } from "@/lib/queries";

const TONE_TEXT = { up: "text-up", down: "text-down", muted: "text-muted" } as const;

export function PortfolioView() {
  const portfolio = usePortfolio();
  const positions = usePositions();
  const strategies = useStrategies();
  const live = useLiveMarkets();
  // 보유 종목 표 필터는 이 카드 안에서 고른다 — 상단 시장 탭은 대시보드·거래 화면에만 보이고 모바일에선 숨는다.
  const [tab, setTab] = useState("all");
  const p = portfolio.data;

  if (portfolio.isLoading) {
    return (
      <div className="flex flex-col gap-4 p-4 md:px-6 md:py-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <CardSkeleton key={i} lines={2} />
          ))}
        </div>
        <CardSkeleton lines={6} />
      </div>
    );
  }
  if (portfolio.isError || !p) {
    return (
      <div className="p-4 md:px-6 md:py-5">
        <ErrorNote>계좌를 불러오지 못했습니다: {reasonText(portfolio.error)}</ErrorNote>
      </div>
    );
  }

  const cash = cashKrw(p);
  const invested = p.total_equity_krw - cash;
  const today = todayPnlView(p);
  const cards = marketCards(p, live);
  const posList = positions.data ?? [];
  const holdings = holdingRows(posList, p, tab);
  const usage = strategyUsage(strategies.data ?? [], posList, p, live);
  const nHeld = cards.reduce((a, c) => a + c.positions, 0);

  return (
    <div className="flex flex-col gap-4 p-4 md:px-6 md:py-5">
      <h1 className="sr-only">포트폴리오</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="총 자산"
          value={fmtKrw(p.total_equity_krw)}
          sub={p.total_includes_us ? "운영 중·저장된 계좌 합계 (원화 환산)" : "미국 제외 (환율 없음)"}
        />
        <KpiCard
          label="현금"
          value={fmtKrw(cash)}
          sub={p.total_equity_krw > 0 ? `총 자산의 ${fmtPct(cash / p.total_equity_krw, { signed: false, digits: 1 })}` : DASH}
        />
        <KpiCard label="보유 평가액" value={fmtKrw(invested)} unit={`${nHeld}종목`} sub="현재가로 평가 (원화 환산)" />
        <KpiCard
          label="오늘 손익"
          value={today.value}
          valueClass={TONE_TEXT[today.tone]}
          sub={today.pct === DASH ? "오늘 스냅샷이 아직 없습니다" : `${today.pct} · 시장 현지 자정부터`}
        />
      </div>

      <section aria-label="시장별 계좌" className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {cards.map((c) => (
          <MarketAccountCard key={c.market} c={c} />
        ))}
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <EquityCard cards={cards} />
        <StrategyUsageCard usage={usage} loading={strategies.isLoading} />
      </div>

      <Card className="flex flex-col gap-3 overflow-x-auto p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[15px] font-semibold">보유 종목</h2>
          <Segmented
            label="보유 종목 시장"
            size="sm"
            value={tab}
            onChange={setTab}
            options={[{ value: "all", label: "전체" }, ...cards.filter((c) => c.active).map((c) => ({ value: c.market, label: c.name }))]}
          />
        </div>
        <p className="text-xs text-muted">
          평가액 큰 순 · 체결 내역은{" "}
          <Link href="/trade" className="text-xs">
            거래 화면
          </Link>
        </p>
        {positions.isError ? <ErrorNote>보유 종목을 불러오지 못했습니다: {reasonText(positions.error)}</ErrorNote> : null}
        {positions.isLoading ? (
          <CardSkeleton lines={4} className="border-0 p-0" />
        ) : holdings.length ? (
          <table className="w-full min-w-[820px] border-collapse text-[13px]">
            <caption className="sr-only">보유 종목: 수량, 평균단가, 현재가, 평가액, 평가손익, 계좌 비중, 손절가까지 거리</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                {["종목", "전략", "수량", "평균단가", "현재가", "평가액", "평가손익", "계좌 비중", "손절까지"].map((h) => (
                  <th key={h} scope="col" className="px-2 pb-2 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {holdings.map((h, i) => (
                <tr key={h.key} className={cx("[&>*]:px-2 [&>*]:py-2.5", i > 0 && "border-t border-bg3")}>
                  <th scope="row" className="text-left font-semibold">
                    <span className="flex items-center gap-2">
                      {h.name}
                      <span className="text-[11px] font-normal text-muted">{cards.find((c) => c.market === h.market)?.name}</span>
                    </span>
                  </th>
                  <td className="text-muted">{h.strategy}</td>
                  <td className="num">{h.qty}</td>
                  <td className="num">{h.avg}</td>
                  <td className="num">{h.price}</td>
                  <td className="num">{h.value}</td>
                  <td className={cx("num", TONE_TEXT[h.tone])}>
                    {h.pnl} <span className="text-[11px]">({h.pnlPct})</span>
                  </td>
                  <td className="num">{h.weight}</td>
                  <td className="num text-muted">{h.stopGap}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyNote>{tab === "all" ? "보유 중인 종목이 없습니다." : "이 시장에 보유 중인 종목이 없습니다."}</EmptyNote>
        )}
        {holdings.some((h) => !h.priced) ? (
          <p className="text-[11px] text-muted">현재가가 없는 종목(—)은 평균단가로 평가했습니다.</p>
        ) : null}
      </Card>
    </div>
  );
}

function MarketAccountCard({ c }: { c: MarketCard }) {
  return (
    <Card as="article" aria-label={`${c.name} 계좌`} className="flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[15px] font-semibold">{c.name}</h2>
        <Badge tone={c.status.tone === "ok" ? "ok" : c.status.tone === "warn" ? "warn" : "muted"}>{c.status.text}</Badge>
      </div>
      {c.active ? (
        <>
          <div className="num text-[22px] font-bold leading-tight">{c.equity}</div>
          <dl className="flex flex-col gap-1.5 text-xs">
            <div className="flex justify-between gap-2">
              <dt className="text-muted">현금</dt>
              <dd className="num">
                {c.cash}
                {c.cashShare != null ? (
                  <span className="text-muted"> ({fmtPct(c.cashShare, { signed: false, digits: 0 })})</span>
                ) : null}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">보유 평가액</dt>
              <dd className="num">
                {c.invested} <span className="text-muted">· {c.positions}종목</span>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">오늘 손익</dt>
              <dd className={cx("num", TONE_TEXT[c.today.tone])}>{c.today.text}</dd>
            </div>
          </dl>
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-xs">
              <span className="text-muted">이번 달</span>
              <span className="num">
                {c.month.value} <span className="text-muted">/ {c.month.limit}</span>
              </span>
            </div>
            <GaugeBar
              widthPct={c.month.widthPct}
              tone={c.month.tone}
              label={`${c.name} 월 손실 한도 사용률 ${Math.round(c.month.widthPct)}%`}
            />
          </div>
          {c.halted ? <ErrorNote>할트: {c.halted} — 새 진입이 멈춰 있습니다</ErrorNote> : null}
          {c.status.text === "2단계 예정" ? (
            <p className="text-[11px] text-muted">저장된 계좌를 보여 줍니다. 실시간 엔진은 2단계에서 연결됩니다.</p>
          ) : null}
        </>
      ) : (
        <EmptyNote>계좌 없음 — 이 시장의 엔진과 계좌는 2단계에서 연결됩니다.</EmptyNote>
      )}
    </Card>
  );
}

function EquityCard({ cards }: { cards: MarketCard[] }) {
  const choices = cards.filter((c) => c.active);
  const [market, setMarket] = useState(choices[0]?.market ?? "upbit");
  return (
    <EquityCurveCard
      market={market}
      subtitle={market === "upbit" ? "전략 합산 vs BTC 보유 (같은 자본)" : `${cards.find((c) => c.market === market)?.name} 계좌 평가액`}
      extra={
        choices.length > 1 ? (
          <Segmented
            label="시장"
            size="sm"
            value={market}
            onChange={setMarket}
            options={choices.map((c) => ({ value: c.market, label: c.name }))}
          />
        ) : null
      }
    />
  );
}

function StrategyUsageCard({ usage, loading }: { usage: ReturnType<typeof strategyUsage>; loading: boolean }) {
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[15px] font-semibold">전략별 배정 대비 투입</h2>
        <Link href="/strategies" className="text-[13px] font-medium no-underline">
          배분 바꾸기 →
        </Link>
      </div>
      <p className="text-xs text-muted">배정 자본 = 그 시장 계좌 평가액 × 배분. 투입 = 지금 보유한 종목의 평가액.</p>
      {loading ? <CardSkeleton lines={3} className="border-0 p-0" /> : null}
      <ul className="flex flex-col gap-3">
        {usage.map((u) => (
          <li key={u.name} className="flex flex-col gap-1.5 text-xs">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[13px] font-semibold">
                {u.label} <span className="text-[11px] font-normal text-muted">배분 {u.allocation}</span>
              </span>
              <span className="num">
                {u.used} <span className="text-muted">/ {u.target}</span>
              </span>
            </div>
            <div
              className="h-1.5 overflow-hidden rounded-[3px] bg-bg3"
              role="meter"
              aria-label={`${u.label} 배정 대비 투입`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={u.ratio == null ? 0 : Math.round(Math.min(u.ratio, 1) * 100)}
            >
              <div
                className={cx("h-full", !u.live ? "bg-bg4" : u.over ? "bg-warn" : "bg-down")}
                style={{ width: `${Math.min(u.ratio ?? 0, 1) * 100}%` }}
              />
            </div>
            <span className={cx("text-[11px]", u.over ? "text-warn-ink" : "text-muted")}>{u.note}</span>
          </li>
        ))}
        {!loading && !usage.length ? <EmptyNote>배분이 잡힌 전략이 없습니다.</EmptyNote> : null}
      </ul>
    </Card>
  );
}
