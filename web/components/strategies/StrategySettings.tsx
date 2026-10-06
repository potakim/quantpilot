"use client";

// 전략 설정 (Strategy.dc.html): 자본 배분 바 → 전략 카드 2×2 → 오른쪽 리스크 규칙(잠김) + AI 판단 설정.
// 엔진이 이 화면의 켜기/끄기·배분·파라미터·임계값을 그대로 따른다 (ADR 0032). 리스크 규칙은 보여 주기만 한다 (불변식 #6).
import { JudgePanel } from "@/components/strategies/JudgePanel";
import { StrategyCard } from "@/components/strategies/StrategyCard";
import { IconLock } from "@/components/ui/Icons";
import { Card, CardSkeleton, ErrorNote, cx } from "@/components/ui/primitives";
import { reasonText } from "@/lib/api";
import { fmtKrw, fmtPct } from "@/lib/format";
import { useHealth, useLiveMarkets, usePortfolio, useSettings, useStrategies } from "@/lib/queries";
import { allocationSegments, allocationSummary, orderStrategies } from "@/lib/strategy";
import type { RiskRulesView, StrategyView } from "@/lib/types";
import { useUi } from "@/lib/ui-store";

// RiskRules 기본값 (execution/risk.py) — /settings를 받기 전 배분 상한 계산에만 쓴다. 화면 표시는 API 값
const MAX_INTRADAY_FALLBACK = 0.2;

export function StrategySettings() {
  const strategies = useStrategies();
  const portfolio = usePortfolio();
  const settings = useSettings();
  const live = useLiveMarkets();
  const appPaper = useHealth().data?.paper;
  const tab = useUi((s) => s.market);
  const list = orderStrategies(strategies.data ?? []);
  const cards = list.filter((s) => tab === "all" || s.market === tab);
  const rules = settings.data?.risk_rules;
  const maxIntraday = rules?.max_intraday_weight ?? MAX_INTRADAY_FALLBACK;

  return (
    <div className="flex flex-col gap-4 p-4 md:px-6 md:py-5 xl:flex-row">
      <h1 className="sr-only">전략 설정</h1>
      <section aria-label="전략" className="flex min-w-0 flex-grow flex-col gap-3.5">
        {strategies.isLoading ? (
          <CardSkeleton lines={2} />
        ) : strategies.isError ? (
          <ErrorNote>전략 목록을 불러오지 못했습니다: {reasonText(strategies.error)}</ErrorNote>
        ) : (
          <AllocationCard list={list} total={portfolio.data?.total_equity_krw ?? null} />
        )}
        <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
          {strategies.isLoading
            ? [0, 1, 2, 3].map((i) => <CardSkeleton key={i} lines={6} />)
            : cards.map((s) => (
                <StrategyCard
                  key={s.name}
                  s={s}
                  all={list}
                  capital={portfolio.data?.by_market[s.market]?.equity ?? null}
                  live={live.has(s.market)}
                  appPaper={appPaper}
                  maxIntraday={maxIntraday}
                />
              ))}
        </div>
        {!strategies.isLoading && !strategies.isError && !cards.length ? (
          <p className="text-xs text-muted">이 시장에 등록된 전략이 없습니다</p>
        ) : null}
      </section>
      <aside aria-label="규칙과 AI 판단 설정" className="flex w-full shrink-0 flex-col gap-3.5 xl:w-[320px]">
        <RiskRulesPanel rules={rules} loading={settings.isLoading} />
        <JudgePanel />
      </aside>
    </div>
  );
}

function AllocationCard({ list, total }: { list: StrategyView[]; total: number | null }) {
  const segments = allocationSegments(list);
  const pct = (v: number) => fmtPct(v, { signed: false, digits: 0 });
  return (
    <Card className="flex flex-col gap-2.5 px-[18px] py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold">
          자본 배분 · 총 <span className="num">{fmtKrw(total)}</span>
        </h2>
        <span className="text-xs text-muted">{allocationSummary(list)}</span>
      </div>
      <div
        role="img"
        aria-label={`자본 배분: ${segments
          .filter((g) => !g.dashed)
          .map((g) => `${g.label} ${pct(g.share)}`)
          .join(", ")}`}
        className="flex h-3.5 gap-0.5 overflow-hidden rounded-[7px]"
      >
        {segments
          .filter((g) => !g.dashed && g.share > 0)
          .map((g) => (
            <span key={g.key} className={g.color} style={{ width: `${g.share * 100}%` }} />
          ))}
      </div>
      <ul className="flex flex-wrap gap-x-[18px] gap-y-1 text-xs text-muted">
        {segments.map((g) => (
          <li key={g.key} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={cx("h-2.5 w-2.5 rounded-[2px]", g.dashed ? "border border-dashed border-muted2" : g.color)}
            />
            {g.label} {pct(g.share)}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RiskRulesPanel({ rules, loading }: { rules: RiskRulesView | undefined; loading: boolean }) {
  const pct = (v: number) => fmtPct(Math.abs(v), { signed: false, digits: 1 });
  const rows: [string, string, boolean?][] = rules
    ? [
        ["거래당 최대 손실", pct(rules.max_loss_per_trade)],
        ["월 누적 손실 한도", `−${pct(rules.monthly_loss_limit)} → 신규 진입 중단`],
        ["종목당 최대 비중", fmtPct(rules.max_symbol_weight, { signed: false, digits: 0 })],
        ["단타 전략 합산 상한", fmtPct(rules.max_intraday_weight, { signed: false, digits: 0 })],
        ["자전거래 · 허수주문 방지", "항상 켜짐", true],
        [`API 오류 연속 ${rules.max_consecutive_api_errors}회`, "진입 중단 + 알림"],
      ]
    : [];
  return (
    <Card className="flex flex-col gap-2.5 px-[18px] py-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <IconLock size={15} className="text-warn" />
          리스크 규칙
        </h2>
        <span className="text-[11px] text-muted">코드 고정</span>
      </div>
      {loading ? <CardSkeleton lines={4} className="border-0 p-0" /> : null}
      {rules ? (
        <dl className="flex flex-col gap-1.5 text-xs">
          {rows.map(([k, v, ok]) => (
            <div key={k} className="flex justify-between gap-2 rounded-[8px] bg-bg3 px-2.5 py-2">
              <dt className="text-ink2">{k}</dt>
              <dd className={ok ? "font-semibold text-ok-ink" : "num"}>{v}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <p className="text-[11px] leading-normal text-muted">
        이 규칙은 리스크 매니저 코드에 고정되어 있어 화면과 AI 모두 바꿀 수 없습니다.
      </p>
    </Card>
  );
}
