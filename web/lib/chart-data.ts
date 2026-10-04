// 차트 데이터 변환 (거래 화면). API 캔들 `{ts,o,h,l,c,v}`(UTC ISO) → Lightweight Charts 봉(초 단위 UTC).

export interface ApiCandle {
  ts: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export const TF_SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "1d": 86400,
};

/** API 캔들 → 봉. 시각이 틀린 행은 버린다. */
export function toBars(rows: ApiCandle[]): Bar[] {
  const out: Bar[] = [];
  for (const r of rows) {
    const t = Date.parse(r.ts);
    if (Number.isNaN(t)) continue;
    out.push({ time: Math.floor(t / 1000), open: r.o, high: r.h, low: r.l, close: r.c, volume: r.v });
  }
  return out;
}

/** 종가 n개 단순 이동평균. */
export function movingAverage(bars: Bar[], n: number): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i]!.close;
    if (i >= n) sum -= bars[i - n]!.close;
    if (i >= n - 1) out.push({ time: bars[i]!.time, value: Number((sum / n).toFixed(10)) });
  }
  return out;
}

/** ts가 속한 봉의 시작 시각. 기준 봉(anchor)의 시작 시각에서 봉 간격을 이어 붙인다 —
 * 일봉의 시작이 UTC 자정이 아니어도(업비트 09:00 KST·현지 자정) API 봉과 같은 경계로 끊는다 (ADR 0031). */
export function bucketStart(anchor: number, tfSeconds: number, tsSec: number): number {
  return anchor + Math.floor((tsSec - anchor) / tfSeconds) * tfSeconds;
}

/** 차트 가격축 표시: 1,000 이상은 정수 + 천 단위 쉼표, 그 아래는 유효 자릿수를 남긴다. */
export function axisPrice(p: number): string {
  const a = Math.abs(p);
  const digits = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 4;
  return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 실시간 체결가 1건을 마지막 봉에 반영한 봉 (새 봉이면 새로 만든다). 과거 틱이면 null. */
export function applyTick(bars: Bar[], tfSeconds: number, tsSec: number, price: number): Bar | null {
  const last = bars[bars.length - 1];
  if (!last) return null;
  if (tsSec < last.time) return null;
  const start = bucketStart(last.time, tfSeconds, tsSec);
  if (start === last.time) {
    return {
      ...last,
      high: Math.max(last.high, price),
      low: Math.min(last.low, price),
      close: price,
    };
  }
  return { time: start, open: price, high: price, low: price, close: price, volume: 0 };
}
