import { describe, expect, it } from "vitest";
import {
  MINUS,
  fmtKrw,
  fmtNumber,
  fmtPct,
  fmtPrice,
  fmtQty,
  fmtSignedKrw,
  fmtUsd,
  kstClock,
  kstDayStartIso,
  kstRelTime,
  kstTime,
  shortSymbol,
  toneOf,
} from "@/lib/format";

describe("부호·퍼센트", () => {
  it("음수는 U+2212, 양수는 +", () => {
    expect(MINUS).toBe("−");
    expect(fmtPct(-0.021)).toBe("−2.1%");
    expect(fmtPct(0.032)).toBe("+3.2%");
    expect(fmtPct(0)).toBe("0.0%");
  });
  it("부호 없이", () => {
    expect(fmtPct(0.24, { signed: false, digits: 0 })).toBe("24%");
    expect(fmtPct(-0.05, { signed: false, digits: 0 })).toBe("−5%");
  });
  it("null·NaN은 —", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(Number.NaN)).toBe("—");
  });
  it("색 톤: 수익 up(빨강), 손실 down(파랑), 0은 muted", () => {
    expect(toneOf(0.01)).toBe("up");
    expect(toneOf(-0.01)).toBe("down");
    expect(toneOf(0)).toBe("muted");
    expect(toneOf(null)).toBe("muted");
  });
});

describe("원화", () => {
  it("정수 원 단위로 반올림, 천 단위 구분", () => {
    expect(fmtKrw(52480000.4)).toBe("₩52,480,000");
    expect(fmtKrw(1309.5)).toBe("₩1,310");
    expect(fmtKrw(-4120)).toBe("−₩4,120");
  });
  it("부호 원화", () => {
    expect(fmtSignedKrw(312400)).toBe("+₩312,400");
    expect(fmtSignedKrw(-312400)).toBe("−₩312,400");
    expect(fmtSignedKrw(0)).toBe("₩0");
  });
  it("null은 —", () => {
    expect(fmtKrw(null)).toBe("—");
  });
});

describe("수량·가격", () => {
  it("코인 수량은 소수 8자리까지 (뒤 0은 자른다)", () => {
    expect(fmtQty(0.484123456789, "upbit")).toBe("0.48412346");
    expect(fmtQty(0.5, "upbit")).toBe("0.5");
    expect(fmtQty(2, "upbit")).toBe("2");
  });
  it("주식 수량은 정수", () => {
    expect(fmtQty(12.7, "krx")).toBe("13");
  });
  it("가격: 원화 시장은 정수, 1000원 미만 코인은 소수 2자리까지", () => {
    expect(fmtPrice(142120000.2, "upbit")).toBe("142,120,000");
    expect(fmtPrice(812.346, "upbit")).toBe("812.35");
    expect(fmtPrice(812.3, "upbit")).toBe("812.3");
    expect(fmtPrice(78400, "krx")).toBe("78,400");
    expect(fmtPrice(512.3, "us")).toBe("$512.30");
  });
  it("달러", () => {
    expect(fmtUsd(0.19)).toBe("$0.19");
    expect(fmtUsd(0.00001, 5)).toBe("$0.00001");
  });
  it("일반 숫자", () => {
    expect(fmtNumber(0.876, 2)).toBe("0.88");
    expect(fmtNumber(null)).toBe("—");
  });
});

describe("시각 (KST)", () => {
  it("UTC ISO → KST HH:mm", () => {
    expect(kstTime("2026-09-28T05:05:00+00:00")).toBe("14:05");
  });
  it("헤더 시계 형식", () => {
    expect(kstClock(new Date("2026-09-28T05:32:10Z"))).toBe("2026-09-28 (월) 14:32 KST");
  });
  it("null은 —", () => {
    expect(kstTime(null)).toBe("—");
  });
  it("같은 날·어제·그 전", () => {
    const now = new Date("2026-09-28T05:32:00Z"); // KST 9/28 14:32
    expect(kstRelTime("2026-09-28T01:00:00Z", now)).toBe("10:00");
    expect(kstRelTime("2026-09-27T13:35:00Z", now)).toBe("어제 22:35");
    expect(kstRelTime("2026-09-26T01:00:00Z", now)).toBe("09-26 10:00");
  });
  it("KST 오늘 0시의 UTC ISO", () => {
    expect(kstDayStartIso(new Date("2026-09-28T05:32:00Z"))).toBe("2026-09-27T15:00:00.000Z");
    expect(kstDayStartIso(new Date("2026-09-28T16:00:00Z"))).toBe("2026-09-28T15:00:00.000Z");
    expect(kstDayStartIso(new Date("2026-09-28T05:32:00Z"), 6)).toBe("2026-09-21T15:00:00.000Z");
  });
});

describe("종목 표시", () => {
  it("KRW-ETH → ETH, 그 밖은 그대로", () => {
    expect(shortSymbol("KRW-ETH")).toBe("ETH");
    expect(shortSymbol("005930")).toBe("005930");
    expect(shortSymbol("QQQ")).toBe("QQQ");
  });
});
