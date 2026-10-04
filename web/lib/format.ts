// 숫자·시각 표시 규칙 (docs/10 §2·§5). 음수는 U+2212, 양수 손익은 +, 원화는 정수 원, 코인 수량은 소수 8자리.
// 시각은 API가 UTC ISO로 주고 화면은 KST(UTC+9, 서머타임 없음)로 보여 준다.

export type Market = "upbit" | "krx" | "us";
export type Tone = "up" | "down" | "muted";

export const MINUS = "−";
export const DASH = "—";

const KST_OFFSET_MS = 9 * 3600 * 1000;
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function ok(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function group(v: number, min: number, max: number): string {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
  }).format(v);
}

function signed(abs: string, neg: boolean, plus: boolean): string {
  if (neg) return `${MINUS}${abs}`;
  return plus ? `+${abs}` : abs;
}

/** 비율(0.012 = 1.2%)을 부호 붙은 퍼센트로. */
export function fmtPct(
  v: number | null | undefined,
  { signed: plus = true, digits = 1 }: { signed?: boolean; digits?: number } = {},
): string {
  if (!ok(v)) return DASH;
  const pct = Number((v * 100).toFixed(digits));
  if (pct === 0) return `${(0).toFixed(digits)}%`;
  return signed(`${Math.abs(pct).toFixed(digits)}%`, pct < 0, plus);
}

/** 원화: 정수 원 단위로 반올림. */
export function fmtKrw(v: number | null | undefined): string {
  if (!ok(v)) return DASH;
  const r = Math.round(v);
  return signed(`₩${group(Math.abs(r), 0, 0)}`, r < 0, false);
}

/** 부호 붙은 원화 (손익). */
export function fmtSignedKrw(v: number | null | undefined): string {
  if (!ok(v)) return DASH;
  const r = Math.round(v);
  if (r === 0) return "₩0";
  return signed(`₩${group(Math.abs(r), 0, 0)}`, r < 0, true);
}

/** 달러. */
export function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (!ok(v)) return DASH;
  return signed(`$${group(Math.abs(v), digits, digits)}`, v < 0, false);
}

/** 일반 숫자 (소수 자리 고정). */
export function fmtNumber(v: number | null | undefined, digits = 2): string {
  if (!ok(v)) return DASH;
  return signed(group(Math.abs(v), digits, digits), v < 0, false);
}

/** 수량: 코인 소수 8자리(뒤 0 제거), 국내주식 정수, 미국주식 소수 4자리. */
export function fmtQty(v: number | null | undefined, market: Market | string): string {
  if (!ok(v)) return DASH;
  const max = market === "upbit" ? 8 : market === "krx" ? 0 : 4;
  return signed(group(Math.abs(v), 0, max), v < 0, false);
}

/** 가격: 원화 시장은 정수(1000원 미만은 소수 2자리까지), 미국은 달러 2자리. */
export function fmtPrice(v: number | null | undefined, market: Market | string): string {
  if (!ok(v)) return DASH;
  if (market === "us") return fmtUsd(v, 2);
  const abs = Math.abs(v);
  const text = abs >= 1000 ? group(Math.round(abs), 0, 0) : group(abs, 0, 2);
  return signed(text, v < 0, false);
}

/** 값의 색 톤: 상승(수익) 빨강, 하락 파랑 (국내 관행). */
export function toneOf(v: number | null | undefined): Tone {
  if (!ok(v) || v === 0) return "muted";
  return v > 0 ? "up" : "down";
}

const pad = (n: number) => String(n).padStart(2, "0");

function kst(d: Date): Date {
  return new Date(d.getTime() + KST_OFFSET_MS);
}

function parse(iso: string | Date | null | undefined): Date | null {
  if (iso == null) return null;
  const d = iso instanceof Date ? iso : new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** UTC ISO → KST HH:mm. */
export function kstTime(iso: string | Date | null | undefined): string {
  const d = parse(iso);
  if (!d) return DASH;
  const k = kst(d);
  return `${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())}`;
}

/** UTC ISO → KST M/D (차트 날짜 눈금). */
export function kstMonthDay(iso: string | Date | null | undefined): string {
  const d = parse(iso);
  if (!d) return DASH;
  const k = kst(d);
  return `${k.getUTCMonth() + 1}/${k.getUTCDate()}`;
}

function kstDayKey(d: Date): number {
  return Math.floor((d.getTime() + KST_OFFSET_MS) / 86_400_000);
}

/** 오늘이면 HH:mm, 어제면 "어제 HH:mm", 그 전이면 MM-DD HH:mm (KST). */
export function kstRelTime(iso: string | Date | null | undefined, now: Date = new Date()): string {
  const d = parse(iso);
  if (!d) return DASH;
  const diff = kstDayKey(now) - kstDayKey(d);
  const hm = kstTime(d);
  if (diff === 0) return hm;
  if (diff === 1) return `어제 ${hm}`;
  const k = kst(d);
  return `${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())} ${hm}`;
}

/** 헤더 시계: 2026-09-28 (월) 14:32 KST. */
export function kstClock(now: Date): string {
  const k = kst(now);
  const date = `${k.getUTCFullYear()}-${pad(k.getUTCMonth() + 1)}-${pad(k.getUTCDate())}`;
  return `${date} (${WEEKDAYS[k.getUTCDay()]}) ${pad(k.getUTCHours())}:${pad(k.getUTCMinutes())} KST`;
}

/** KST 기준 (오늘 − daysBack)일 0시를 UTC ISO로 — API `from=` 필터용. */
export function kstDayStartIso(now: Date = new Date(), daysBack = 0): string {
  const day = kstDayKey(now) - daysBack;
  return new Date(day * 86_400_000 - KST_OFFSET_MS).toISOString();
}

/** KRW-ETH → ETH (업비트 원화 마켓 접두어 제거). */
export function shortSymbol(symbol: string): string {
  return symbol.startsWith("KRW-") ? symbol.slice(4) : symbol;
}
