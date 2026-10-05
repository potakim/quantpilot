"use client";

// 대시보드 (Main.dc.html, 모바일은 Mobile.dc.html). 숫자는 전부 API 응답이고, 값이 없으면 "—"로 둔다 (ADR 0020, lib/metrics.ts).
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { HaltBanner } from "@/components/dashboard/HaltBanner";
import { GaugeBar, useMonthLoss } from "@/components/layout/MonthLossCard";
import { EquityCurveCard } from "@/components/ui/EquityCurveCard";
import { IconSparkle } from "@/components/ui/Icons";
import { JudgmentCard } from "@/components/ui/JudgmentCard";
import { KpiCard } from "@/components/ui/KpiCard";
import { Toggle } from "@/components/ui/Toggle";
import { Badge, Card, CardSkeleton, EmptyNote, ErrorNote, cx } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { DASH, fmtKrw, fmtNumber, fmtPct, fmtQty, fmtUsd, kstDayStartIso, kstTime, shortSymbol, toneOf } from "@/lib/format";
import { STRATEGY_TITLE, marketLabel, scheduleSource, strategyLabel } from "@/lib/labels";
import { cashKrw, curvePaths, scheduleRows, shortWhat, strategyMddText, strategyMonthText, todayPnlView } from "@/lib/metrics";
import {
  useEquityCurve,
  useFills,
  useGates,
  useHealth,
  useJudgments,
  usePortfolio,
  useSchedule,
  useStrategies,
} from "@/lib/queries";
import { PAPER_ONLY } from "@/lib/strategy";
import type { JudgmentRow, StrategyView } from "@/lib/types";
import { useUi } from "@/lib/ui-store";
import { useLive } from "@/lib/ws";

const TONE_TEXT = { up: "text-up", down: "text-down", muted: "text-muted" } as const;

function positionText(s: StrategyView): string {
  const entries = Object.entries(s.status.position ?? {}).filter(([, q]) => q);
  if (!entries.length) return "없음";
  return entries.map(([sym, q]) => `${shortSymbol(sym)} ${fmtQty(q, s.market)}`).join(" · ");
}

function judgmentStats(items: JudgmentRow[]) {
  const n = items.length;
  const entries = items.filter((j) => j.gate !== "hold").length;
  const avg = n ? items.reduce((a, j) => a + j.confidence, 0) / n : null;
  const cost = items.reduce(
    (a, j) => a + (j.cost_usd ?? 0) + j.verdicts.reduce((b, v) => b + (v.cost_usd ?? 0), 0),
    0,
  );
  return { n, entries, holds: n - entries, avg, cost };
}

function useToggleStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      apiFetch<StrategyView>(`/strategies/${name}`, { method: "PATCH", json: { enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });
}

function KpiRow() {
  const health = useHealth();
  const portfolio = usePortfolio();
  const strategies = useStrategies();
  const gates = useGates();
  const [today] = useState(() => kstDayStartIso());
  const judgments = useJudgments({ from: today, limit: 500 });
  const fills = useFills({ from: today, limit: 500 });
  if (portfolio.isLoading || strategies.isLoading) {
    return (
      <div className="grid grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <CardSkeleton key={i} lines={2} />
        ))}
      </div>
    );
  }
  const p = portfolio.data;
  const paper = health.data?.paper ?? true;
  const list = strategies.data ?? [];
  const running = list.filter((s) => s.enabled).length;
  const js = judgmentStats(judgments.data?.items ?? []);
  const todayFills = (fills.data?.items ?? []).filter((f) => !f.shadow);
  const fees = todayFills.reduce((a, f) => a + f.fee + f.tax, 0);
  const paperOnly = list.filter((s) => s.paper && !paper).map((s) => strategyLabel(s.name));
  const g2 = gates.data?.g2.pass;
  const pnl = todayPnlView(p);
  return (
    <div className="grid grid-cols-4 gap-4">
      <KpiCard
        label={`총 자산 (${paper ? "페이퍼" : "실전"})`}
        value={p ? fmtKrw(p.total_equity_krw) : DASH}
        sub={
          p
            ? `현금 ${fmtKrw(cashKrw(p))}${!p.total_includes_us ? " · 미국 제외 (환율 없음)" : ""}`
            : "포트폴리오를 불러오지 못했습니다"
        }
      />
      <KpiCard
        label="오늘 손익"
        value={
          <span
            className={TONE_TEXT[pnl.tone]}
            title={pnl.value === DASH ? "오늘(시장 자정 이후) 평가액 스냅샷이 아직 없습니다" : undefined}
          >
            {pnl.value}
          </span>
        }
        unit={pnl.pct === DASH ? undefined : pnl.pct}
        sub={`체결 ${todayFills.length}건 · 수수료 ${fmtKrw(fees)}`}
      />
      <KpiCard
        label="실행 중 전략"
        value={running}
        unit={`/ ${list.length}`}
        sub={
          paperOnly.length
            ? `${paperOnly.join(", ")}는 페이퍼 검증 중`
            : paper
              ? `전 전략 페이퍼 · 관문 G2 ${g2 === undefined ? "확인 중" : g2 ? "통과" : "미통과"}`
              : "전 전략 실전"
        }
      />
      <KpiCard
        ai
        label={
          <>
            <IconSparkle size={14} className="text-ai" />
            AI 판단 오늘
          </>
        }
        value={`${js.n}회`}
        unit={`진입 ${js.entries} · 보류 ${js.holds}`}
        sub={`판단 모델 평균 확신도 ${fmtNumber(js.avg, 2)} · 비용 ${fmtUsd(js.cost)}`}
      />
    </div>
  );
}

function ScheduleCard() {
  const statuses = useLive((s) => s.strategyStatus);
  const schedule = useSchedule();
  const now = Date.now();
  const items = scheduleRows(schedule.data, Object.values(statuses), now);
  return (
    <Card className="flex flex-col gap-3.5 p-5">
      <h2 className="text-[15px] font-semibold">오늘 일정 (KST)</h2>
      {schedule.isLoading && !items.length ? (
        <CardSkeleton lines={4} className="border-0 p-0" />
      ) : items.length ? (
        <ol className="flex flex-col gap-3">
          {items.map((s, i) => {
            const done = s.done;
            const next = !done && items.findIndex((x) => !x.done) === i;
            const what = shortWhat(s.what);
            return (
              <li key={s.key} className="flex items-start gap-3">
                <span className={cx("num w-12 text-[13px]", next ? "font-semibold text-ink" : "text-muted")}>
                  {kstTime(s.at)}
                </span>
                <span
                  aria-hidden="true"
                  className={cx(
                    "mt-1 h-2.5 w-2.5 rounded-full",
                    done ? "bg-ok" : next ? "bg-ai" : "border-2 border-bench",
                  )}
                />
                <div className="flex flex-col gap-0.5">
                  <div className="text-[13px] font-medium" title={what.text === what.full ? undefined : what.full}>
                    {what.text}
                  </div>
                  <div className="text-xs text-muted">
                    {scheduleSource(s.name, s.market)} · {done ? "완료" : next ? "다음" : "예정"}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <EmptyNote>
          {schedule.isError ? `일정을 불러오지 못했습니다: ${reasonText(schedule.error)}` : "오늘 남은 예약 작업이 없습니다."}
        </EmptyNote>
      )}
    </Card>
  );
}

function StrategyTable() {
  const strategies = useStrategies();
  const health = useHealth();
  const market = useUi((s) => s.market);
  const toggle = useToggleStrategy();
  // "페이퍼만": 실전 전환이 잠긴 전략(ORB, 원본 아트보드) + 실전 모드인데 아직 페이퍼로 도는 전략
  const paper = health.data?.paper ?? true;
  const rows = (strategies.data ?? []).filter((s) => market === "all" || s.market === market);
  return (
    <Card className="flex min-h-0 flex-col gap-3 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-[15px] font-semibold">활성 전략</h2>
        <Link href="/strategies" className="text-[13px] font-medium no-underline">
          전략 설정 →
        </Link>
      </div>
      {toggle.isError ? <ErrorNote>{reasonText(toggle.error)}</ErrorNote> : null}
      {strategies.isLoading ? (
        <CardSkeleton lines={4} className="border-0 p-0" />
      ) : strategies.isError ? (
        <ErrorNote>전략 목록을 불러오지 못했습니다: {reasonText(strategies.error)}</ErrorNote>
      ) : (
        <table className="w-full table-fixed border-collapse text-[13px]">
          <caption className="sr-only">활성 전략 목록</caption>
          <colgroup>
            <col style={{ width: "24.6%" }} />
            <col style={{ width: "15.4%" }} />
            <col style={{ width: "12.3%" }} />
            <col style={{ width: "18.5%" }} />
            <col style={{ width: "15.4%" }} />
            <col style={{ width: "13.8%" }} />
          </colgroup>
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              {["전략", "시장", "상태", "포지션", "이번 달", "MDD"].map((h) => (
                <th key={h} scope="col" className="px-2 pb-2 font-normal">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => {
              // 토글은 저장값 — 엔진 값(WS strategy.status)은 5초 늦게 따라와서 누른 직후 되돌아 보인다 (ADR 0034)
              const enabled = s.enabled;
              return (
                <tr key={s.name} className={cx("[&>*]:px-2 [&>*]:py-2.5", i > 0 && "border-t border-bg3")}>
                  <th scope="row" className="text-left font-semibold">
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {STRATEGY_TITLE[s.name] ?? s.name}
                      {PAPER_ONLY[s.name] || (s.paper && !paper) ? <Badge tone="warn">페이퍼만</Badge> : null}
                    </span>
                  </th>
                  <td className="text-muted">
                    {marketLabel(s.market)} · {s.symbols.length}종목
                  </td>
                  <td>
                    <Toggle
                      checked={enabled}
                      label={`${strategyLabel(s.name)} ${enabled ? "끄기" : "켜기"}`}
                      disabled={toggle.isPending}
                      onChange={(next) => toggle.mutate({ name: s.name, enabled: next })}
                    />
                  </td>
                  <td className={Object.keys(s.status.position ?? {}).length ? "" : "text-muted"}>{positionText(s)}</td>
                  <td className={cx("num", TONE_TEXT[toneOf(s.status.month_pnl)])}>{strategyMonthText(s.status.month_pnl)}</td>
                  <td className="num text-muted">{strategyMddText(s.status.mdd_30d)}</td>
                </tr>
              );
            })}
            {!rows.length ? (
              <tr>
                <td colSpan={6} className="px-2 py-3 text-xs text-muted">
                  이 시장에 등록된 전략이 없습니다
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RecentJudgments({ count = 3 }: { count?: number }) {
  const q = useJudgments({ limit: 5 });
  const items = (q.data?.items ?? []).slice(0, count);
  return (
    <Card ai className="flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold">
          <IconSparkle size={16} className="text-ai" />
          최근 AI 판단
        </h2>
        <Link href="/judgments" className="text-[13px] font-medium no-underline">
          전체 로그 →
        </Link>
      </div>
      {q.isLoading ? <CardSkeleton lines={3} className="border-0 p-0" /> : null}
      {q.isError ? <ErrorNote>{reasonText(q.error)}</ErrorNote> : null}
      {!q.isLoading && !items.length && !q.isError ? <EmptyNote>기록된 판단이 없습니다</EmptyNote> : null}
      <div className="flex flex-col gap-2.5">
        {items.map((j) => (
          <JudgmentCard key={j.id} j={j} />
        ))}
      </div>
    </Card>
  );
}

/** 모바일 총 자산 카드의 스파크라인 (Mobile.dc.html). 자산 곡선은 업비트 계좌뿐이라 그 사실을 같이 적는다. */
function Sparkline() {
  const curve = useEquityCurve("upbit", 30);
  const p = curvePaths(curve.data?.points ?? [], null, 300, 40);
  if (!p) return null;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-[11px] text-muted">
        <span>업비트 30일</span>
        <span className="num">{fmtPct(p.ret, { digits: 1 })}</span>
      </div>
      <svg viewBox="0 -3 300 46" preserveAspectRatio="none" aria-hidden="true" className="h-9 w-full">
        <polyline
          points={p.main}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
          className="text-muted2"
        />
      </svg>
    </div>
  );
}

function MobileDashboard() {
  const portfolio = usePortfolio();
  const strategies = useStrategies();
  const toggle = useToggleStrategy();
  const judgments = useJudgments({ limit: 5 });
  const { gauge } = useMonthLoss();
  const p = portfolio.data;
  const pnl = todayPnlView(p);
  // 원본처럼 켜진 전략만. 꺼진 전략은 개수만 알려 주고 전략 설정에서 켠다
  const all = useMemo(() => strategies.data ?? [], [strategies.data]);
  const active = all.filter((s) => s.enabled);
  const off = all.length - active.length;
  return (
    <div className="flex flex-col gap-3 px-5 pb-4 pt-1 md:hidden">
      <HaltBanner />
      {portfolio.isLoading ? (
        <CardSkeleton lines={3} />
      ) : (
        <Card className="flex flex-col gap-2.5 rounded-[16px] p-[18px]">
          <div className="text-xs text-muted">총 자산</div>
          <div className="num text-[28px] font-bold">{p ? fmtKrw(p.total_equity_krw) : DASH}</div>
          <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs">
            <span className={TONE_TEXT[pnl.tone]}>
              오늘 {pnl.value}
              {pnl.pct === DASH ? "" : ` (${pnl.pct})`}
            </span>
            <span className="text-muted">이번 달 {gauge.value}</span>
          </div>
          <Sparkline />
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-[11px] text-muted">
              <span>월 손실 {gauge.limit}</span>
              <span className="num">{gauge.value}</span>
            </div>
            <GaugeBar widthPct={gauge.widthPct} tone={gauge.tone} height={5} label={`월 손실 한도 사용률 ${Math.round(gauge.widthPct)}%`} />
          </div>
        </Card>
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">활성 전략</h2>
        <Link href="/strategies" className="flex min-h-[44px] items-center text-xs font-medium no-underline">
          설정 →
        </Link>
      </div>
      {toggle.isError ? <ErrorNote>{reasonText(toggle.error)}</ErrorNote> : null}
      <Card as="div" className="flex flex-col">
        <ul>
          {active.map((s, i) => (
            <li key={s.name} className={cx("flex items-center gap-3 px-3.5 py-3", i < active.length - 1 && "border-b border-bg3")}>
              <div className="flex flex-grow flex-col gap-0.5">
                <span className="text-[13px] font-semibold">{strategyLabel(s.name)}</span>
                <span className="text-[11px] text-muted">
                  {marketLabel(s.market)} · {positionText(s)}
                </span>
              </div>
              <span className={cx("num text-xs", TONE_TEXT[toneOf(s.status.month_pnl)])}>{strategyMonthText(s.status.month_pnl)}</span>
              <Toggle
                size="lg"
                checked={s.enabled}
                label={`${strategyLabel(s.name)} ${s.enabled ? "끄기" : "켜기"}`}
                disabled={toggle.isPending}
                onChange={(next) => toggle.mutate({ name: s.name, enabled: next })}
              />
            </li>
          ))}
        </ul>
        {strategies.isLoading ? <CardSkeleton lines={3} className="border-0" /> : null}
        {strategies.isError ? (
          <div className="p-3.5">
            <ErrorNote>전략 목록을 불러오지 못했습니다: {reasonText(strategies.error)}</ErrorNote>
          </div>
        ) : null}
        {!strategies.isLoading && !strategies.isError && !active.length ? (
          <p className="px-3.5 py-3 text-xs text-muted">켜진 전략이 없습니다.</p>
        ) : null}
        {off > 0 ? (
          <p className={cx("px-3.5 py-2.5 text-[11px] text-muted", active.length > 0 && "border-t border-bg3")}>
            꺼진 전략 {off}개는{" "}
            <Link href="/strategies" className="text-[11px]">
              전략 설정
            </Link>
            에서 켤 수 있습니다
          </p>
        ) : null}
      </Card>
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <IconSparkle size={14} className="text-ai" />
          최근 AI 판단
        </h2>
        <Link href="/judgments" className="flex min-h-[44px] items-center text-xs font-medium no-underline">
          전체 →
        </Link>
      </div>
      <div className="flex flex-col gap-2">
        {(judgments.data?.items ?? []).slice(0, 2).map((j) => (
          <JudgmentCard key={j.id} j={j} compact />
        ))}
        {judgments.isLoading ? <CardSkeleton lines={2} /> : null}
        {!judgments.isLoading && !(judgments.data?.items ?? []).length ? <EmptyNote>기록된 판단이 없습니다</EmptyNote> : null}
      </div>
    </div>
  );
}

export function Dashboard() {
  return (
    <>
      <h1 className="sr-only">대시보드</h1>
      <MobileDashboard />
      <div className="hidden flex-col gap-5 p-6 md:flex">
        <KpiRow />
        <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4">
          <EquityCurveCard />
          <ScheduleCard />
        </div>
        <div className="flex flex-col gap-3">
          <HaltBanner />
        </div>
        <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4">
          <StrategyTable />
          <RecentJudgments />
        </div>
      </div>
    </>
  );
}
