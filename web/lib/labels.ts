// 판단·전략·시장 표시 문구. 색만으로 뜻을 전하지 않도록 문구와 톤을 함께 준다 (docs/10 §5).

export type LabelTone = "ok" | "warn" | "muted" | "ai" | "up";

export const MARKET_LABEL: Record<string, string> = {
  upbit: "업비트",
  krx: "국내주식",
  us: "미국주식",
};

// 전략 표시 이름 (아트보드 문구). 모르는 이름은 코드 이름 그대로.
export const STRATEGY_LABEL: Record<string, string> = {
  vol_breakout: "변동성 돌파",
  gem: "듀얼 모멘텀 GEM",
  gtaa: "10개월선 GTAA",
  orb: "5분 ORB",
};

export const STRATEGY_TITLE: Record<string, string> = {
  vol_breakout: "변동성 돌파 + 이평 필터",
  gem: "듀얼 모멘텀 GEM (6/9/12)",
  gtaa: "10개월선 자산배분 GTAA",
  orb: "5분 ORB",
};

export const strategyLabel = (name: string | null | undefined) =>
  name ? (STRATEGY_LABEL[name] ?? name) : "—";

export const marketLabel = (m: string | null | undefined) => (m ? (MARKET_LABEL[m] ?? m) : "—");

/** 일정 항목의 출처: 전략이면 전략 이름, 아니면 시장(없으면 "공통"). 잡 ID를 화면에 내지 않는다 (ADR 0031). */
export const scheduleSource = (name: string, market: string | null | undefined) =>
  STRATEGY_LABEL[name] ?? (market ? (MARKET_LABEL[market] ?? market) : "공통");

/** 거래 화면 AI 패널의 게이팅 결과. */
export function gateLabel(gate: string | null | undefined): { text: string; tone: LabelTone } {
  if (gate === "full") return { text: "진입 허용 · 전체 사이징", tone: "ok" };
  if (gate === "half") return { text: "진입 허용 · 절반 사이징", tone: "ok" };
  if (gate === "hold") return { text: "보류", tone: "warn" };
  return { text: gate ?? "—", tone: "muted" };
}

/** 로그·카드의 결과 칸. */
export function judgmentResult(gate: string | null | undefined): { text: string; tone: LabelTone } {
  if (gate === "full") return { text: "진입 100%", tone: "ok" };
  if (gate === "half") return { text: "진입 50%", tone: "ok" };
  if (gate === "hold") return { text: "보류", tone: "warn" };
  return { text: gate ?? "—", tone: "muted" };
}

/** LLM 판정 배지. 모델 id에서 표시 이름을 뽑는다. */
export function verdictLabel(v: { model: string; approve: boolean }): {
  name: string;
  short: string;
  text: string;
  approve: boolean;
} {
  const low = v.model.toLowerCase();
  const name = low.includes("claude") ? "Claude" : low.includes("gemini") ? "Gemini" : v.model;
  return { name, short: name.slice(0, 1).toUpperCase(), text: v.approve ? "승인" : "보류", approve: v.approve };
}

// 낮을수록 좋은 리스크 질문 (06 문서 원자 질문). 0.5 이상이면 주황.
const RISK_KEYS = new Set(["news_risk", "liquidity_stress", "event_ahead", "already_priced"]);

export interface AnswerRow {
  key: string;
  text: string;
  full: string;
  width: number;
  tone: LabelTone;
}

const two = (v: number) => v.toFixed(2);
const clamp01 = (v: number) => Math.round(Math.max(0, Math.min(1, v)) * 1e4) / 1e4;

/** 판단 모델 답(answers) → 표시 행. regime 같은 분포는 최고 확률 라벨, signal_quality는 5점 만점. */
export function answerRows(answers: Record<string, unknown> | null | undefined): AnswerRow[] {
  const rows: AnswerRow[] = [];
  for (const [key, value] of Object.entries(answers ?? {})) {
    if (typeof value === "number" && Number.isFinite(value)) {
      if (key === "signal_quality" || value > 1) {
        const text = `${value.toFixed(1)} / 5`;
        rows.push({ key, text, full: text, width: clamp01(value / 5), tone: "ai" });
      } else {
        const risky = RISK_KEYS.has(key);
        const tone: LabelTone = risky ? (value >= 0.5 ? "warn" : "ok") : "ai";
        rows.push({ key, text: two(value), full: two(value), width: clamp01(value), tone });
      }
    } else if (value && typeof value === "object" && !Array.isArray(value)) {
      const entries = Object.entries(value as Record<string, unknown>).filter(
        (e): e is [string, number] => typeof e[1] === "number",
      );
      if (!entries.length) continue;
      entries.sort((a, b) => b[1] - a[1]);
      const [top, p] = entries[0]!;
      rows.push({
        key,
        text: `${top} ${two(p)}`,
        full: entries.map(([k, v]) => `${k} ${two(v)}`).join(" · "),
        width: clamp01(p),
        tone: "ai",
      });
    }
  }
  return rows;
}
