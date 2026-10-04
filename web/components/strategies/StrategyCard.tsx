"use client";

// 전략 카드 (Strategy.dc.html 2×2): 켜기/끄기 · 파라미터(ParamSpec 그대로) · 배정 자본 · G1 요약 · 버튼.
// 바꾼 값은 PATCH /strategies/{name}으로 저장하고, 엔진이 5초 안에 읽는다 (ADR 0032).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { displaySymbol } from "@/components/trade/Watchlist";
import { Toggle } from "@/components/ui/Toggle";
import { Button, Chip, FlagChip, Slider, SymbolChip, buttonClass } from "@/components/ui/controls";
import { Badge, Card, ErrorNote } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { fmtKrw, fmtPct, fmtUsd } from "@/lib/format";
import { STRATEGY_TITLE, marketLabel, strategyLabel } from "@/lib/labels";
import {
  PAPER_ONLY,
  STRATEGY_SUBTITLE,
  allocationMax,
  formatParam,
  g1Summary,
  paramControls,
  paramsDiffer,
  snap,
} from "@/lib/strategy";
import type { StrategyView } from "@/lib/types";

type Patch = { enabled?: boolean; params?: Record<string, unknown>; allocation?: number };

function useStrategyMutations(name: string, onDone: () => void) {
  const qc = useQueryClient();
  const settle = {
    onSuccess: (view: StrategyView) =>
      qc.setQueryData<StrategyView[]>(["strategies"], (old) => old?.map((x) => (x.name === view.name ? view : x))),
    onSettled: () => {
      onDone();
      void qc.invalidateQueries({ queryKey: ["strategies"] });
    },
  };
  const patch = useMutation({
    mutationFn: (body: Patch) => apiFetch<StrategyView>(`/strategies/${name}`, { method: "PATCH", json: body }),
    ...settle,
  });
  const reset = useMutation({
    mutationFn: () => apiFetch<StrategyView>(`/strategies/${name}/reset-params`, { method: "POST" }),
    ...settle,
  });
  return { patch, reset };
}

/** capital = 그 시장 계좌 평가액. 엔진의 배정 자본 = 평가액 × 배분과 같은 기준 (ADR 0032 §5) */
export function StrategyCard({
  s,
  all,
  capital,
  live,
  maxIntraday,
}: {
  s: StrategyView;
  all: StrategyView[];
  capital: number | null;
  live: boolean;
  maxIntraday: number;
}) {
  // 끄는 중인 값 (놓기 전까지 화면에만). 저장이 끝나면 서버 값으로 돌아간다
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [allocDraft, setAllocDraft] = useState<number | null>(null);
  const { patch, reset } = useStrategyMutations(s.name, () => {
    setDraft(null);
    setAllocDraft(null);
  });
  const busy = patch.isPending || reset.isPending;
  const params = { ...s.params, ...draft };
  const controls = paramControls(s.schema, params);
  const alloc = allocDraft ?? s.allocation;
  const allocMax = allocationMax(all, s.name, maxIntraday);
  const g1 = g1Summary(s.gate.g1?.evidence);
  const g2Rule = PAPER_ONLY[s.name];
  const title = STRATEGY_TITLE[s.name] ?? s.name;
  const symbolsLabel = s.market === "upbit" ? "대상 코인" : "자산";
  const intraday = s.horizon === "intraday";
  const error = patch.error ?? reset.error;

  const setParam = (name: string, value: unknown) => setDraft((d) => ({ ...d, [name]: value }));
  const commitParam = (name: string, value: unknown) => patch.mutate({ params: { [name]: value } });

  return (
    <Card
      as="article"
      aria-label={title}
      line={g2Rule ? "border-warn-line" : undefined}
      className="flex flex-col gap-2.5 px-[18px] py-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h2 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {title}
            {g2Rule ? <Badge tone="warn">페이퍼 전용</Badge> : null}
            {!live ? <Badge tone="muted">2단계 예정</Badge> : null}
          </h2>
          <p className="text-[11px] text-muted">{STRATEGY_SUBTITLE[s.name] ?? marketLabel(s.market)}</p>
        </div>
        <Toggle
          checked={s.enabled}
          label={`${strategyLabel(s.name)} ${s.enabled ? "끄기" : "켜기"}`}
          disabled={busy || !live}
          onChange={(next) => patch.mutate({ enabled: next })}
        />
      </div>

      <div className="flex flex-col gap-2 text-xs">
        {controls.map((c) =>
          c.kind === "slider" ? (
            <Slider
              key={c.spec.name}
              label={c.label}
              value={c.value}
              min={c.spec.min!}
              max={c.spec.max!}
              step={c.spec.step!}
              display={formatParam(c.spec, c.value)}
              disabled={busy}
              onChange={(v) => setParam(c.spec.name, snap(v, c.spec))}
              onCommit={(v) => {
                const next = snap(v, c.spec);
                if (next !== s.params[c.spec.name]) commitParam(c.spec.name, next);
              }}
            />
          ) : (
            <div key={c.spec.name} className="flex items-center justify-between gap-3">
              <span className="shrink-0 text-muted">{c.label}</span>
              {c.kind === "chips" ? (
                <span className="flex flex-wrap justify-end gap-1">
                  {c.chips.map((v) => (
                    <Chip key={v}>{v}</Chip>
                  ))}
                </span>
              ) : c.kind === "flag" ? (
                <FlagChip
                  on={c.value}
                  label={c.label}
                  disabled={busy}
                  onChange={(next) => commitParam(c.spec.name, next)}
                />
              ) : (
                <span className="num">{c.text}</span>
              )}
            </div>
          ),
        )}
        <div className="flex items-start justify-between gap-3">
          <span className="shrink-0 text-muted">{symbolsLabel}</span>
          <span className="flex flex-wrap justify-end gap-1">
            {s.symbols.map((sym) => (
              <SymbolChip key={sym}>{displaySymbol(s.market, sym).replace("/KRW", "")}</SymbolChip>
            ))}
          </span>
        </div>
        <Slider
          label="배정 자본"
          value={alloc}
          min={0}
          max={Math.max(allocMax, alloc)}
          step={0.01}
          valueWidth="w-[132px]"
          display={`${fmtPct(alloc, { signed: false, digits: 0 })} · ${
            !live || capital == null ? "2단계" : s.market === "us" ? fmtUsd(alloc * capital) : fmtKrw(alloc * capital)
          }`}
          disabled={busy || (allocMax <= 0 && alloc <= 0)}
          onChange={(v) => setAllocDraft(Math.min(Number(v.toFixed(2)), allocMax))}
          onCommit={(v) => {
            const next = Math.min(Number(v.toFixed(2)), allocMax);
            if (next !== s.allocation) patch.mutate({ allocation: next });
          }}
        />
        {intraday ? (
          <p className="text-[11px] text-muted">
            단타 합산 상한 {fmtPct(maxIntraday, { signed: false, digits: 0 })} · 이 전략은 최대{" "}
            {fmtPct(allocMax, { signed: false, digits: 0 })}
          </p>
        ) : null}
      </div>

      {g2Rule ? (
        <div className="flex flex-col gap-1.5 rounded-block bg-warn-bg px-3 py-2.5 text-xs">
          <span className="font-semibold text-warn-ink">실전 전환 관문 G2</span>
          <span className="text-ink2">
            조건: {g2Rule}. {live ? "페이퍼 기록으로 판정합니다." : "시장 엔진이 연결되는 2단계에서 페이퍼 기록으로 판정합니다."}
          </span>
        </div>
      ) : g1 ? (
        <div className="flex flex-wrap justify-between gap-2 rounded-block bg-bg3 px-3 py-2.5 text-xs">
          <span className="text-muted">백테스트 {g1.period} (G1 검증)</span>
          <span className="num">{g1.text}</span>
        </div>
      ) : (
        <p className="rounded-block bg-bg3 px-3 py-2.5 text-xs text-muted">
          검증 기록 없음 — 백테스트 화면에서 실행해 확인합니다
        </p>
      )}
      {paramsDiffer(s.schema, s.params) ? (
        <p className="text-[11px] text-warn-ink">기본값과 다른 파라미터입니다. G1은 기본값으로 검증했습니다.</p>
      ) : null}
      {error ? <ErrorNote>{reasonText(error)}</ErrorNote> : null}

      <div className="mt-auto flex gap-2">
        <Link href={`/backtests?strategy=${s.name}`} className={buttonClass("outline", "flex-grow")}>
          {g2Rule ? "페이퍼 성과 보기" : "백테스트 보기"}
        </Link>
        {g2Rule ? (
          <Button variant="ghost" disabled>
            실전 전환 (잠김)
          </Button>
        ) : (
          <Button variant="ghost" disabled={busy || !paramsDiffer(s.schema, s.params)} onClick={() => reset.mutate()}>
            기본값 복원
          </Button>
        )}
      </div>
    </Card>
  );
}
