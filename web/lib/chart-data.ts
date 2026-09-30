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

/** 실시간 체결가 1건을 마지막 봉에 반영한 봉 (새 봉이면 새로 만든다). 과거 틱이면 null. */
export function applyTick(bars: Bar[], tfSeconds: number, tsSec: number, price: number): Bar | null {
  const last = bars[bars.length - 1];
  if (!last) return null;
  const start = Math.floor(tsSec / tfSeconds) * tfSeconds;
  if (start < last.time) return null;
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
