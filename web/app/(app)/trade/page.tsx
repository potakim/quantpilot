import type { Metadata } from "next";
import { TradeIndex } from "@/components/trade/TradeIndex";

export const metadata: Metadata = { title: "거래 · 차트" };

export default function TradeIndexPage() {
  return <TradeIndex />;
}
