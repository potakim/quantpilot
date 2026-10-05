// 전략 설정 화면(Strategy.dc.html)의 계산: 배분 바·배분 상한·파라미터 표시·G1 요약·AI 설정 변경 여부.
// 화면 컴포넌트는 그리기만 하고, 숫자 규칙은 여기서 정한다 (테스트: tests/strategy.test.ts).
import { fmtNumber, fmtPct } from "@/lib/format";
import { STRATEGY_LABEL } from "@/lib/labels";
import type { G1Evidence, JudgeView, ParamSpecView, StrategyView } from "@/lib/types";

/** 카드 순서 = 화면 원본 2×2 (장기 위, 단타 아래). 모르는 전략은 뒤에. */
export const STRATEGY_ORDER = ["gem", "vol_breakout", "gtaa", "orb"];

export function orderStrategies(list: StrategyView[]): StrategyView[] {
  const rank = (n: string) => (STRATEGY_ORDER.includes(n) ? STRATEGY_ORDER.indexOf(n) : STRATEGY_ORDER.length);
  return [...list].sort((a, b) => rank(a.name) - rank(b.name));
}

/** 카드 부제 (화면 원본 문구). 모르는 전략은 보유 기간·시장으로 만든다. */
export const STRATEGY_SUBTITLE: Record<string, string> = {
  gem: "장기 · KIS 해외주식 · 월 1회",
  vol_breakout: "단타 1일 보유 · 업비트 · 매일 09:00 청산",
  gtaa: "장기 · KIS 국내 ETF · 월 1회",
  orb: "단타 당일 청산 · Alpaca paper · QQQ",
};

/** 페이퍼 전용 전략과 실전 전환 관문 G2 조건 (05 §4). 카드에 주황 테두리 + 잠긴 실전 전환 버튼. */
export const PAPER_ONLY: Record<string, string> = {
  orb: "슬리피지 2¢ 포함 샤프 > 0.5",
};

// 배분 바 색 (화면 원본): 장기 파랑 두 가지, 단타는 강조색(보라), 현금은 bg-4
const SEGMENT_COLOR: Record<string, string> = {
  gem: "bg-down",
  gtaa: "bg-down-2",
  vol_breakout: "bg-ai",
};
const SEGMENT_FALLBACK = ["bg-down", "bg-down-2", "bg-ai", "bg-ok"];

export interface Segment {
  key: string;
  label: string;
  share: number; // 0~1
  color: string; // tailwind 배경 클래스
  dashed: boolean; // 배분 0 — 범례에만 점선으로
}

const HORIZON_RANK: Record<string, number> = { long: 0, swing: 1, intraday: 2 };

/** 배분 바 조각: 배분 있는 전략(장기 → 스윙 → 단타) → 현금 → 배분 0(점선, 범례만). */
export function allocationSegments(list: StrategyView[]): Segment[] {
  const funded = list
    .filter((s) => s.allocation > 0)
    .sort((a, b) => (HORIZON_RANK[a.horizon] ?? 1) - (HORIZON_RANK[b.horizon] ?? 1));
  const out: Segment[] = funded.map((s, i) => ({
    key: s.name,
    label: STRATEGY_LABEL[s.name] ?? s.name,
    share: s.allocation,
    color: SEGMENT_COLOR[s.name] ?? SEGMENT_FALLBACK[i % SEGMENT_FALLBACK.length]!,
    dashed: false,
  }));
  const used = funded.reduce((a, s) => a + s.allocation, 0);
  out.push({ key: "cash", label: "현금", share: Math.max(0, 1 - used), color: "bg-bg4", dashed: false });
  for (const s of list.filter((x) => x.allocation <= 0)) {
    out.push({ key: s.name, label: STRATEGY_LABEL[s.name] ?? s.name, share: 0, color: "", dashed: true });
  }
  return out;
}

/** "장기 75% · 단타 15% · 현금 10%" — 보유 기간별 합. */
export function allocationSummary(list: StrategyView[]): string {
  const sum = (pred: (s: StrategyView) => boolean) => list.filter(pred).reduce((a, s) => a + s.allocation, 0);
  const intraday = sum((s) => s.horizon === "intraday");
  const longer = sum((s) => s.horizon !== "intraday");
  const cash = Math.max(0, 1 - intraday - longer);
  const pct = (v: number) => fmtPct(v, { signed: false, digits: 0 });
  return `장기 ${pct(longer)} · 단타 ${pct(intraday)} · 현금 ${pct(cash)}`;
}

/** 이 전략에 줄 수 있는 최대 배분: 전체 합 ≤ 1, 단타 합 ≤ 단타 상한 (API PATCH 검사와 같은 규칙). */
export function allocationMax(list: StrategyView[], name: string, maxIntraday: number): number {
  const me = list.find((s) => s.name === name);
  if (!me) return 0;
  const others = list.filter((s) => s.name !== name);
  let room = 1 - others.reduce((a, s) => a + s.allocation, 0);
  if (me.horizon === "intraday") {
    const otherIntraday = others.filter((s) => s.horizon === "intraday").reduce((a, s) => a + s.allocation, 0);
    room = Math.min(room, maxIntraday - otherIntraday);
  }
  return Math.max(0, Math.floor(room * 100 + 1e-6) / 100);
}

// ---------- 파라미터 ----------

/** 화면 원본의 짧은 이름. 없으면 ParamSpec 설명, 그것도 없으면 코드 이름. */
const PARAM_LABEL: Record<string, string> = {
  k: "K 값",
  target_vol: "목표 변동성",
  ma_windows: "이평 스코어 필터",
  noise_k: "노이즈 K",
  max_weight: "코인당 최대 비중",
  lookbacks: "룩백 앙상블",
  ma_days: "이평 기간",
  risk_per_trade: "거래당 리스크",
  target_r: "목표 배수",
  allow_short: "공매도",
  exit_time: "강제 청산 (미국 동부)",
};

// 비율로 보여 줄 파라미터 (값 0.01 = 1%)
const PERCENT_PARAMS = new Set(["target_vol", "risk_per_trade", "max_weight"]);
const CHIP_UNIT: Record<string, string> = { lookbacks: "개월" };

export function paramLabel(spec: ParamSpecView): string {
  return PARAM_LABEL[spec.name] ?? (spec.description || spec.name);
}

const decimals = (step: number | null) => {
  if (!step) return 2;
  const s = String(step);
  return s.includes(".") ? s.split(".")[1]!.length : 0;
};

/** 파라미터 값 표시: 비율은 %, 목표 배수는 R, 이평 기간은 일, 그 밖은 step 자릿수. */
export function formatParam(spec: ParamSpecView, value: unknown): string {
  if (typeof value !== "number") return String(value ?? "—");
  if (PERCENT_PARAMS.has(spec.name)) {
    return fmtPct(value, { signed: false, digits: Math.max(0, decimals(spec.step) - 2) }); // step 0.001 → 0.1%
  }
  if (spec.name === "target_r") return `${fmtNumber(value, 0)}R`;
  if (spec.name === "ma_days") return `${fmtNumber(value, 0)}일`;
  return fmtNumber(value, decimals(spec.step));
}

export type ParamControl =
  | { kind: "slider"; spec: ParamSpecView; label: string; value: number }
  | { kind: "flag"; spec: ParamSpecView; label: string; value: boolean }
  | { kind: "chips"; spec: ParamSpecView; label: string; chips: string[] }
  | { kind: "text"; spec: ParamSpecView; label: string; text: string };

/** ParamSpec → 화면 줄. 숫자 범위 = 슬라이더, 참·거짓 = 칩 토글, 목록 = 칩(읽기 전용), 설명 있는 글자 = 한 줄. */
export function paramControls(schema: ParamSpecView[] | undefined, params: Record<string, unknown>): ParamControl[] {
  const out: ParamControl[] = [];
  for (const spec of schema ?? []) {
    const value = params[spec.name] ?? spec.default;
    const label = paramLabel(spec);
    if (typeof value === "number" && spec.min != null && spec.max != null && spec.step) {
      out.push({ kind: "slider", spec, label, value });
    } else if (typeof value === "boolean") {
      out.push({ kind: "flag", spec, label, value });
    } else if (Array.isArray(value)) {
      const unit = CHIP_UNIT[spec.name] ?? "";
      out.push({ kind: "chips", spec, label, chips: value.map((v) => `${String(v)}${unit}`) });
    } else if (typeof value === "string" && value && spec.description) {
      out.push({ kind: "text", spec, label, text: value });
    }
  }
  return out;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** 지금 파라미터가 기본값과 다른가 — G1은 기본값으로 검증했다 (ADR 0032 §6). */
export function paramsDiffer(schema: ParamSpecView[] | undefined, params: Record<string, unknown>): boolean {
  return (schema ?? []).some((spec) => spec.name in params && !same(params[spec.name], spec.default));
}

/** 슬라이더 값을 step 격자에 맞춘다 (부동소수 오차 제거). */
export function snap(value: number, spec: Pick<ParamSpecView, "min" | "step">): number {
  const step = spec.step ?? 0;
  if (!step) return value;
  const base = spec.min ?? 0;
  return Number((base + Math.round((value - base) / step) * step).toFixed(decimals(step) + 2));
}

// ---------- G1 요약 ----------

/** 카드의 백테스트 요약 블록: G1 근거의 기간·연수익률·최대낙폭. 근거가 없으면 null. */
export function g1Summary(e: G1Evidence | undefined): { period: string; text: string } | null {
  const m = e?.metrics;
  if (!m || !e?.period) return null;
  const mdd = m.mdd ?? m.mdd_monthly;
  const parts = [];
  if (typeof m.cagr === "number") parts.push(`CAGR ${fmtPct(m.cagr)}`);
  if (typeof mdd === "number") parts.push(`MDD ${fmtPct(mdd)}`);
  return parts.length ? { period: e.period, text: parts.join(" · ") } : null;
}

// ---------- AI 판단 설정 ----------

/** LLM 리뷰어 2개 (불변식 #8: 2모델 합의). 끈 자리는 stub. */
export function llmModels(claude: boolean, gemini: boolean): string[] {
  return [claude ? "claude" : "stub", gemini ? "gemini" : "stub"];
}

/** AI 판단 설정 저장 후 안내 — 키마다 적용 시점이 다르다 (ADR 0032·0035). */
export function judgeSavedNote(keys: string[]): string {
  const notes: string[] = [];
  if (keys.some((k) => k === "judge.provider" || k === "llm.models")) notes.push("판단 모델·리뷰어는 엔진을 다시 켜면 적용됩니다.");
  if (keys.some((k) => k.startsWith("gate."))) notes.push("임계값은 엔진이 5초 안에 반영합니다.");
  if (keys.includes("news.enabled")) notes.push("뉴스 요약은 다음 정시 수집부터 적용됩니다.");
  return notes.join(" ");
}

/** 저장한 판단 모델·리뷰어가 엔진이 쓰는 것과 다르다 = 재시작해야 적용된다. 엔진 기록이 없으면 모름(false). */
export function restartPending(j: JudgeView | undefined): boolean {
  if (!j?.active) return false;
  return j.active.provider !== j.provider || !same([...j.active.llm_models].sort(), [...j.llm_models].sort());
}
