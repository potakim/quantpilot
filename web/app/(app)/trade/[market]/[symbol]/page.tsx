import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { TradeView } from "@/components/trade/TradeView";

const MARKETS = new Set(["upbit", "krx", "us"]);

type Params = { params: Promise<{ market: string; symbol: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `거래 · ${decodeURIComponent(symbol)}` };
}

export default async function TradePage({ params }: Params) {
  const { market, symbol } = await params;
  const sym = decodeURIComponent(symbol);
  if (!MARKETS.has(market) || !/^[A-Za-z0-9._-]{1,32}$/.test(sym)) notFound();
  return <TradeView market={market} symbol={sym} />;
}
