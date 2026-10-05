"use client";

// AI 판단 설정 (Strategy.dc.html 오른쪽 아래). 임계값은 엔진이 5초 안에 읽고, 판단 모델·리뷰어는 엔진을
// 다시 켤 때 적용된다 (ADR 0032). 키가 없는 모델은 고를 수 없다 — API도 KEY_MISSING으로 거부한다.
// 뉴스 요약(Gemini Flash-Lite, news.enabled)은 다음 정시 수집부터 적용되고, 끄면 제목만 요약한다 (ADR 0035).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { IconSparkle } from "@/components/ui/Icons";
import { Button, CheckRow, ChoiceCard, Slider } from "@/components/ui/controls";
import { Badge, Card, CardSkeleton, ErrorNote } from "@/components/ui/primitives";
import { apiFetch, reasonText } from "@/lib/api";
import { fmtNumber, fmtUsd } from "@/lib/format";
import { useCostsAi, useSettings } from "@/lib/queries";
import { judgeSavedNote, llmModels, restartPending } from "@/lib/strategy";

interface Draft {
  provider?: string;
  claude?: boolean;
  gemini?: boolean;
  news?: boolean;
  hold?: number;
  full?: number;
}

export function JudgePanel() {
  const qc = useQueryClient();
  const settings = useSettings();
  const costs = useCostsAi();
  const [draft, setDraft] = useState<Draft>({});
  const [saved, setSaved] = useState<string[] | null>(null);
  const j = settings.data?.judge;
  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      apiFetch<{ updated: string[] }>("/settings", { method: "PATCH", json: patch }),
    onSuccess: (r) => {
      setDraft({});
      setSaved(r.updated);
      void qc.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  if (settings.isLoading) return <CardSkeleton lines={6} />;
  if (!j) {
    return (
      <Card ai className="flex flex-col gap-2.5 p-[18px]">
        <h2 className="text-sm font-semibold">AI 판단 설정</h2>
        <ErrorNote>설정을 불러오지 못했습니다{settings.isError ? `: ${reasonText(settings.error)}` : ""}</ErrorNote>
      </Card>
    );
  }

  const provider = draft.provider ?? j.provider;
  const claude = draft.claude ?? j.llm_models.includes("claude");
  const gemini = draft.gemini ?? j.llm_models.includes("gemini");
  const news = draft.news ?? j.news_summary;
  const hold = draft.hold ?? j.hold_below;
  const full = draft.full ?? j.full_above;
  const models = llmModels(claude, gemini);
  const patch: Record<string, unknown> = {};
  if (provider !== j.provider) patch["judge.provider"] = provider;
  if (JSON.stringify(models) !== JSON.stringify(j.llm_models)) patch["llm.models"] = models;
  if (hold !== j.hold_below) patch["gate.hold_below"] = hold;
  if (full !== j.full_above) patch["gate.full_above"] = full;
  if (news !== j.news_summary) patch["news.enabled"] = news;
  const changed = Object.keys(patch).length > 0;
  const badGate = hold >= full;
  const keyNote = (has: boolean | undefined) => (has ? "키 등록됨" : "키 필요");
  const set = (d: Draft) => {
    setSaved(null);
    setDraft((cur) => ({ ...cur, ...d }));
  };

  return (
    <Card line="border-ai-line-2" className="flex flex-grow flex-col gap-2.5 p-[18px]">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <IconSparkle size={15} className="text-ai" />
          AI 판단 설정
        </h2>
        {restartPending(j) ? <Badge tone="warn">엔진 재시작 후 적용</Badge> : null}
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-xs text-muted">판단 모델 · 엔진 재시작 때 적용</legend>
        <ChoiceCard
          name="judge"
          value="typesafe"
          checked={provider === "typesafe"}
          onChange={(v) => set({ provider: v })}
          disabled={!j.keys.typesafe && provider !== "typesafe"}
          title="TypeSafe Jev (jev-latest)"
          meta={j.keys.typesafe ? <span className="num">$0.042/M</span> : keyNote(false)}
        />
        <ChoiceCard
          name="judge"
          value="laya"
          checked={provider === "laya"}
          onChange={(v) => set({ provider: v })}
          disabled
          title="Laya 자체 호스팅 (파인튜닝 필요)"
          meta="2단계"
        />
        <ChoiceCard
          name="judge"
          value="stub"
          checked={provider === "stub"}
          onChange={(v) => set({ provider: v })}
          title="스텁 (개발용 · AI 판단 없이 통과)"
          meta="무료"
        />
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs text-muted">확신도 임계값 · 바로 적용</legend>
        <Slider
          label="보류 미만"
          labelWidth="w-[80px]"
          valueWidth="w-[40px]"
          value={hold}
          min={0.3}
          max={0.7}
          step={0.05}
          display={fmtNumber(hold, 2)}
          onChange={(v) => set({ hold: Number(v.toFixed(2)) })}
        />
        <Slider
          label="전량 이상"
          labelWidth="w-[80px]"
          valueWidth="w-[40px]"
          value={full}
          min={0.7}
          max={0.98}
          step={0.01}
          display={fmtNumber(full, 2)}
          onChange={(v) => set({ full: Number(v.toFixed(2)) })}
        />
        {badGate ? <p className="text-[11px] text-warn-ink">보류 기준은 전량 기준보다 낮아야 합니다.</p> : null}
      </fieldset>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-xs text-muted">LLM 합의 (2/2 필요) · 엔진 재시작 때 적용</legend>
        <CheckRow
          checked={claude}
          onChange={(v) => set({ claude: v })}
          disabled={!j.keys.claude && !claude}
          title="Claude Sonnet 5"
          meta={j.keys.claude ? "진입 합의 · 사후 리뷰" : keyNote(false)}
        />
        <CheckRow
          checked={gemini}
          onChange={(v) => set({ gemini: v })}
          disabled={!j.keys.gemini && !gemini}
          title="Gemini 3.5 Flash"
          meta={j.keys.gemini ? "진입 합의" : keyNote(false)}
        />
        <CheckRow
          checked={news}
          onChange={(v) => set({ news: v })}
          disabled={!j.keys.gemini && !news}
          title="Gemini 3.5 Flash-Lite"
          meta={j.keys.gemini ? "뉴스 요약 · 1시간" : keyNote(false)}
        />
        <p className="text-[11px] leading-relaxed text-muted">
          끈 자리는 스텁 리뷰어가 채웁니다. 뉴스 요약을 끄면 제목만 저장해 판단 모델이 읽습니다(다음 정시부터). 키는
          설정 · API 키 화면에서 등록합니다.
        </p>
      </fieldset>

      <div className="mt-auto flex justify-between gap-2 rounded-block bg-bg3 px-3 py-2.5 text-xs">
        <span className="text-muted">이번 달 AI 비용</span>
        <span className="num">
          {costs.data ? fmtUsd(costs.data.total_usd) : costs.isError ? "불러오지 못함" : "…"}
        </span>
      </div>
      {save.isError ? <ErrorNote>{reasonText(save.error)}</ErrorNote> : null}
      {saved ? (
        <p role="status" className="text-[11px] text-ok-ink">
          저장했습니다. {judgeSavedNote(saved)}
        </p>
      ) : null}
      <Button variant="primary" disabled={!changed || badGate || save.isPending} onClick={() => save.mutate(patch)}>
        {save.isPending ? "저장 중…" : "설정 저장"}
      </Button>
    </Card>
  );
}
