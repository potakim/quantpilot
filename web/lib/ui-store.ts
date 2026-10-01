"use client";

// 화면 상태 (서버 데이터가 아닌 것): 헤더 시장 탭.
import { create } from "zustand";

export type MarketTab = "all" | "upbit" | "krx" | "us";

export const MARKET_TABS: { value: MarketTab; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "upbit", label: "코인" },
  { value: "krx", label: "국내주식" },
  { value: "us", label: "미국주식" },
];

export const useUi = create<{ market: MarketTab; setMarket: (m: MarketTab) => void }>((set) => ({
  market: "all",
  setMarket: (market) => set({ market }),
}));
