"use client";

// 백테스트 (Backtest.dc.html): 왼쪽 설정 → POST /backtests → 진행률(WS `backtest:{id}`, 끊기면 3초 재조회) →
// 오른쪽 결과. 주소 `?strategy=&id=`로 전략·실행을 고정한다 (전략 카드의 "백테스트 보기"가 strategy를 넘긴다).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { BacktestForm, type RunForm } from "@/components/backtests/BacktestForm";
import { BacktestResult } from "@/components/backtests/BacktestResult";
import { Card, CardSkeleton, EmptyNote, ErrorNote } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { sourcesFor } from "@/lib/backtest";
import { useLiveChannels } from "@/lib/live";
import { useBacktest, useBacktests, useStrategies } from "@/lib/queries";
import { orderStrategies } from "@/lib/strategy";
import { useLive } from "@/lib/ws";

export function BacktestView({ initialStrategy, initialId }: { initialStrategy?: string; initialId?: number }) {
  const router = useRouter();
  const qc = useQueryClient();
  const strategies = useStrategies();
  const list = useMemo(() => orderStrategies(strategies.data ?? []), [strategies.data]);
  const [form, setForm] = useState<RunForm | null>(null);
  const [id, setId] = useState<number | null>(initialId ?? null);
  const [compareId, setCompareId] = useState<number | null>(null);

  // 전략 목록이 오면 주소의 전략(없으면 첫 카드)으로 설정을 채운다
  useEffect(() => {
    if (form || !list.length) return;
    const s = list.find((x) => x.name === initialStrategy) ?? list[0]!;
    setForm({ strategy: s.name, source: sourcesFor(s)[0]!, start: "", end: "" });
  }, [form, list, initialStrategy]);

  const strategy = list.find((x) => x.name === form?.strategy);
  const history = useBacktests(form?.strategy);
  const all = useBacktests();
  const finished = useMemo(() => (history.data ?? []).filter((h) => Object.keys(h.metrics ?? {}).length > 0), [history.data]);

  // 고른 실행(방금 시작한 실행 포함)이 없으면 그 전략의 최근 실행. 전략을 바꾸면 id를 비운다
  const selected = id ?? finished[0]?.id ?? null;
  const run = useBacktest(selected ?? null);
  const compare = useBacktest(compareId);

  const live = useLive((s) => (selected != null ? s.backtests[String(selected)] : undefined));
  const running = run.data?.status === "queued" || run.data?.status === "running";
  useLiveChannels(running && selected != null ? [`backtest:${selected}`] : []);
  useEffect(() => {
    if (!live?.done) return;
    void qc.invalidateQueries({ queryKey: ["backtest", selected] });
    void qc.invalidateQueries({ queryKey: ["backtests"] });
    void qc.invalidateQueries({ queryKey: ["strategies"] }); // 시도 횟수
  }, [live?.done, selected, qc]);

  useEffect(() => {
    if (!form) return;
    const q = new URLSearchParams({ strategy: form.strategy });
    if (selected != null) q.set("id", String(selected));
    router.replace(`/backtests?${q}`, { scroll: false });
  }, [form, selected, router]);

  const start = useMutation({
    mutationFn: (f: RunForm) =>
      apiFetch<{ id: number; status: string }>("/backtests", {
        method: "POST",
        json: {
          strategy: f.strategy,
          params: strategy?.params ?? {},
          source: f.source,
          start: f.start ? `${f.start}-01` : undefined,
          end: f.end || undefined,
        },
      }),
    onSuccess: (r) => {
      setId(r.id);
      setCompareId(null);
      void qc.invalidateQueries({ queryKey: ["backtests"] });
    },
  });

  if (strategies.isLoading || !form) {
    return (
      <div className="flex flex-col gap-4 p-4 md:px-6 md:py-5 lg:flex-row">
        <CardSkeleton lines={8} className="lg:w-[300px]" />
        <CardSkeleton lines={6} className="flex-grow" />
      </div>
    );
  }
  if (strategies.isError) {
    return (
      <div className="p-4 md:px-6 md:py-5">
        <ErrorNote>전략 목록을 불러오지 못했습니다: {reasonText(strategies.error)}</ErrorNote>
      </div>
    );
  }

  const progress = live?.progress ?? run.data?.progress ?? null;
  const compareChoices = (all.data ?? []).filter((h) => h.id !== selected && Object.keys(h.metrics ?? {}).length > 0);
  const failed = run.data?.status === "failed";

  return (
    <div className="flex flex-col gap-4 p-4 md:px-6 md:py-5 lg:flex-row">
      <BacktestForm
        strategies={list}
        form={form}
        onChange={(f) => {
          if (f.strategy !== form.strategy) {
            setId(null);
            setCompareId(null);
          }
          setForm(f);
        }}
        onRun={() => start.mutate(form)}
        running={start.isPending || running}
        progress={progress}
        error={start.isError ? reasonText(start.error) : null}
      />
      {run.data && !running && !failed && run.data.equity ? (
        <BacktestResult
          run={run.data}
          strategy={list.find((x) => x.name === run.data!.strategy)}
          history={finished}
          onSelect={(n) => {
            setId(n);
            if (n === compareId) setCompareId(null);
          }}
          compare={compare.data?.equity ? compare.data : null}
          compareChoices={compareChoices}
          onCompare={setCompareId}
        />
      ) : (
        <section aria-label="백테스트 결과" className="flex min-w-0 flex-grow flex-col gap-3.5">
          <h1 className="text-[15px] font-semibold">백테스트</h1>
          <Card className="p-5">
            {running ? (
              <div role="status" className="flex flex-col gap-2 text-xs text-muted">
                <span>
                  #{selected} 실행 중 · {Math.round((progress ?? 0) * 100)}%
                </span>
                <span className="block h-1.5 overflow-hidden rounded-[3px] bg-bg0">
                  <span className="block h-1.5 bg-ai" style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
                </span>
              </div>
            ) : failed ? (
              <ErrorNote>실행 #{selected} 실패: {run.data?.error ?? "원인 미상"}</ErrorNote>
            ) : run.isLoading || history.isLoading ? (
              <CardSkeleton lines={4} className="border-0 p-0" />
            ) : (
              <EmptyNote>
                이 전략의 실행 기록이 없습니다. 설정에서 데이터·기간을 고르고 ‘백테스트 실행’을 누르세요.
              </EmptyNote>
            )}
          </Card>
        </section>
      )}
    </div>
  );
}
